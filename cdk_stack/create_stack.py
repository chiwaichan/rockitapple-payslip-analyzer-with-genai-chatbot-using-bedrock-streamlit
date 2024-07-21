from constructs import Construct
import os
from aws_cdk import (
    Stack,
    CfnOutput,
    RemovalPolicy,
    Duration, 
    CfnParameter,   
    aws_lambda as lambda_,
    aws_iam as iam,
    aws_s3 as s3,
    aws_s3_notifications as s3_notifications,
    aws_athena as athena,
    aws_glue as glue,
    aws_ecr as ecr,
    aws_ecs as ecs,
    aws_ecr_assets as ecr_assets,
    aws_ecs_patterns as ecs_patterns
)
import amazon_textract_idp_cdk_constructs as tcdk


class CreateStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        script_location = os.path.dirname(__file__)

        # Build and push Docker image to ECR repository
        docker_image_asset = ecr_assets.DockerImageAsset(self, "StreamlitDockerImage",
            directory=os.path.join(script_location, '../containers/streamlit')
        )

        image_uri = docker_image_asset.image_uri

        # Define Fargate Task Definition
        fargate_task_definition = ecs.FargateTaskDefinition(self, "StreamlitTaskDefinition", cpu=2048, memory_limit_mib=4096)

        ecr_policy_statement = iam.PolicyStatement(
            actions=[
                "ecr:GetAuthorizationToken",
                "ecr:BatchCheckLayerAvailability", 
                "ecr:GetDownloadUrlForLayer", 
                "ecr:BatchGetImage" 
            ],
            resources=["*"]
        )
        
        # Attach the policy statement to the task execution role
        fargate_task_definition.add_to_execution_role_policy(ecr_policy_statement)

        athena_policy_statement = iam.PolicyStatement(
            actions=[
                "athena:ListDatabases",
                "athena:ListTableMetadata",
                "athena:GetTableMetadata",
                "athena:GetDatabase",
                "athena:GetWorkGroup",
                "athena:GetQueryExecution",
                "athena:GetQueryResults",
                "athena:StartQueryExecution",
                "athena:StopQueryExecution",
                "glue:GetDatabase",
                "glue:GetDatabases",
                "glue:GetTable",
                "glue:GetTables",
                "glue:GetTableVersion",
                "glue:GetTableVersions",
            ],
            resources=["*"]  # Adjust this based on your resource needs
        )
        fargate_task_definition.add_to_task_role_policy(athena_policy_statement)

        
        # create an s3 bucket as a resource and make it delete files on bucket delete
        s3_bucket_for_athena = s3.Bucket(self,
                                "S3BucketForAthena",
                                versioned=True,
                                removal_policy=RemovalPolicy.DESTROY, auto_delete_objects=True)
        
        s3_bucket_for_athena_bucket_arn = s3_bucket_for_athena.bucket_arn

        s3_bucket_for_athena_s3_path = f"s://{s3_bucket_for_athena.bucket_name}"  


        s3_output_for_athena_policy_statement = iam.PolicyStatement(
            actions=[
                "s3:CreateBucket",
                "s3:GetBucketLocation",
                "s3:ListBucket",
                "s3:ListBucketMultipartUploads",
                "s3:GetObject",
                "s3:AbortMultipartUpload",
                "s3:PutObject",
                "s3:ListMultipartUploadParts"
            ],
            resources=[s3_bucket_for_athena_bucket_arn, f"{s3_bucket_for_athena_bucket_arn}/*"],
        )
        fargate_task_definition.add_to_task_role_policy(s3_output_for_athena_policy_statement)


        bedrock_policy_statement = iam.PolicyStatement(
            actions=[
                "bedrock:InvokeModel"
            ],
            resources=["*"],
        )
        fargate_task_definition.add_to_task_role_policy(bedrock_policy_statement)





        container = fargate_task_definition.add_container(
            "StreamlitContainer",
            image=ecs.ContainerImage.from_registry(image_uri),
            memory_limit_mib=4096,
            cpu=2048,
            logging=ecs.LogDrivers.aws_logs(stream_prefix="Streamlit")
        )
        container.add_port_mappings(
            ecs.PortMapping(container_port=8501, protocol=ecs.Protocol.TCP)
        )

        # Define Fargate Service
        fargate_service = ecs_patterns.ApplicationLoadBalancedFargateService(self, "StreamlitFargateService",
            task_definition=fargate_task_definition,
            public_load_balancer=True
        )
        
        
        
        
        # Outputs
        CfnOutput(self, "FargateServiceURL",
            value=fargate_service.load_balancer.load_balancer_dns_name
        )










        payslip_athena_data_bucket = s3.Bucket(self,
            "PayslipAthenaDataBucket",
            removal_policy=RemovalPolicy.DESTROY,
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            auto_delete_objects=True,
        )

        payslip_athena_data_bucket_bucket_arn = payslip_athena_data_bucket.bucket_arn

        payslip_athena_data_bucket_policy_statement = iam.PolicyStatement(
            actions=[
                "athena:StartQueryExecution",
                "athena:GetQueryExecution",
                "athena:GetQueryResults",
                "athena:ListWorkGroups",
                "athena:GetWorkGroup",
                "s3:GetObject",
                "s3:ListBucket",
                "s3:PutObject",
                "s3:GetBucketLocation"
            ],
            resources=[
                payslip_athena_data_bucket.bucket_arn, 
                f"{payslip_athena_data_bucket.bucket_arn}/*"
            ],
        )
        fargate_task_definition.add_to_task_role_policy(payslip_athena_data_bucket_policy_statement)




        athena_database = glue.CfnDatabase(self,
            "AthenaDatabase",
            catalog_id=self.account,
            database_input=glue.CfnDatabase.DatabaseInputProperty(
                name="payslip_database"
            )
        )

        athena_table = glue.CfnTable(self,
            "AthenaTable",
            catalog_id=self.account,
            database_name=athena_database.ref,
            table_input=glue.CfnTable.TableInputProperty(
                name="payslips",
                table_type="EXTERNAL_TABLE",
                storage_descriptor=glue.CfnTable.StorageDescriptorProperty(
                    columns=[ 
                        glue.CfnTable.ColumnProperty(name="holiday_pay_amount", type="double"),
                        glue.CfnTable.ColumnProperty(name="kiwisaver_employee_deduction_total_amount", type="double"),
                        glue.CfnTable.ColumnProperty(name="address", type="string"),
                        glue.CfnTable.ColumnProperty(name="direct_credit_total_amount", type="double"),
                        glue.CfnTable.ColumnProperty(name="bank_account", type="string"),
                        glue.CfnTable.ColumnProperty(name="kiwisaver_employer_contribution_amount", type="double"),
                        glue.CfnTable.ColumnProperty(name="earnings_ordinary_time_standard_work_hours", type="string"),
                        glue.CfnTable.ColumnProperty(name="name", type="string"),
                        glue.CfnTable.ColumnProperty(name="pay_date", type="string"),
                        glue.CfnTable.ColumnProperty(name="earnings_ordinary_time_standard_work_rate_amount", type="double"),
                        glue.CfnTable.ColumnProperty(name="alternative_leave_days", type="string"),
                        glue.CfnTable.ColumnProperty(name="total_deductions_amount", type="double"),
                        glue.CfnTable.ColumnProperty(name="pay_period_start", type="string"),
                        glue.CfnTable.ColumnProperty(name="pay_period_end", type="string"),
                        glue.CfnTable.ColumnProperty(name="total_earnings_amount", type="double"),
                        glue.CfnTable.ColumnProperty(name="take_home_pay_amount", type="double"),
                        glue.CfnTable.ColumnProperty(name="ird_number", type="string"),
                        glue.CfnTable.ColumnProperty(name="earnings_ordinary_time_standard_work_total_amount", type="double"),
                        glue.CfnTable.ColumnProperty(name="kiwisaver_employee_deduction_percentage", type="string"),
                        glue.CfnTable.ColumnProperty(name="tax_deductions_amount", type="double"),
                        glue.CfnTable.ColumnProperty(name="employee_id", type="int"),
                    ],
                    location=f"s3://{payslip_athena_data_bucket.bucket_name}/",
                    input_format="org.apache.hadoop.mapred.TextInputFormat",
                    output_format="org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat",
                    serde_info=glue.CfnTable.SerdeInfoProperty(
                        serialization_library="org.openx.data.jsonserde.JsonSerDe"
                    )
                )
            )
        )




        source_bucket_payslips = s3.Bucket(self, "source-payslips", auto_delete_objects=True, removal_policy=RemovalPolicy.DESTROY)


        # Create IAM policy for Bedrock InvokeModel permission
        bedrock_invoke_model_policy = iam.PolicyStatement(
            actions=["bedrock:InvokeModel"],
            resources=["*"],
            effect=iam.Effect.ALLOW
        )

        bucket_arn = f"arn:aws:s3:::{source_bucket_payslips.bucket_name}" 
        # Create IAM policy for Bedrock InvokeModel permission
        s3_model_policy = iam.PolicyStatement(
            actions=["s3:GetObject", "s3:GetBucketLocation"],
            resources=[bucket_arn, f"{bucket_arn}/*"],
            effect=iam.Effect.ALLOW
        )

        bucket_arn_athena_table = f"arn:aws:s3:::{payslip_athena_data_bucket.bucket_name}" 
        s3_athena_table_policy = iam.PolicyStatement(
            actions=["s3:PutObject"],
            resources=[bucket_arn_athena_table, f"{bucket_arn_athena_table}/*"],
            effect=iam.Effect.ALLOW
        )

        textract_model_policy = iam.PolicyStatement(
            actions=["textract:StartDocumentAnalysis", "textract:GetDocumentAnalysis"],
            resources=["*"],
            effect=iam.Effect.ALLOW
        )

        # Create IAM role for the Lambda function
        lambda_role = iam.Role(
            self,
            "UploadContentFunctionRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")
            ]
        )

        # Attach the Bedrock InvokeModel policy to the Lambda role
        lambda_role.add_to_policy(bedrock_invoke_model_policy)
        lambda_role.add_to_policy(s3_model_policy)
        lambda_role.add_to_policy(s3_athena_table_policy)
        lambda_role.add_to_policy(textract_model_policy)

        upload_content_function = lambda_.DockerImageFunction(
            self,
            f"UploadContentFunction",
            code=lambda_.DockerImageCode.from_image_asset(
                os.path.join(script_location, '../containers/extract_content')
            ),
            memory_size=128,
            timeout=Duration.seconds(900),
            architecture=lambda_.Architecture.X86_64,
            environment={
                'S3_BUCKET_NAME_ATHENA': payslip_athena_data_bucket.bucket_name
            },
            role=lambda_role  # Assign the IAM role to the Lambda function
        )

        source_bucket_payslips.grant_read(upload_content_function)

        # get s3 new object to trigger the lambda function
        source_bucket_payslips.add_event_notification(
            s3.EventType.OBJECT_CREATED,
            s3_notifications.LambdaDestination(upload_content_function)
        )

        CfnOutput(self, "UploadPayslip",
            value=f'aws s3 rm s3://{source_bucket_payslips.bucket_name}/ --recursive && aws s3 cp "Payslip - 2024-02-21.pdf" s3://{source_bucket_payslips.bucket_name}/file.pdf',
            description="Name of the S3 bucket"
        )






        container.add_environment(
            "S3_BUCKET", f's3://{s3_bucket_for_athena.bucket_name}'
        )
        container.add_environment(
            "ATHENA_DATABASE_NAME", athena_database.database_input.name
        )
        