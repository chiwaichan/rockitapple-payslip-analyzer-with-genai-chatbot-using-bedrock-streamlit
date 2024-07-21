import json
import requests
import boto3
import urllib.parse


import os
import json
import datetime
from textractprettyprinter.t_pretty_print import Textract_Pretty_Print, get_string, get_tables_string, Pretty_Print_Table_Format
from trp.t_pipeline import pipeline_merge_tables
import trp.trp2 as t2
from textractcaller.t_call import call_textract, Textract_Features, QueriesConfig, Query
from trp.trp2 import TDocument, TDocumentSchema
from trp.t_tables import MergeOptions, HeaderFooterType
import boto3
import pandas as pd
from trp import Document
from textractprettyprinter.t_pretty_print import convert_table_to_list
# from textractcaller import call_textract, call_textract_analyzeid, QueriesConfig, Query

textract_client = boto3.client('textract', region_name='us-east-1')

bedrock = boto3.client(
  service_name='bedrock-runtime', 
  region_name="us-east-1"
)


def do_my_custom_call_textract(s3_uri_of_documents, queries_config):
   textract_json = call_textract(input_document=s3_uri_of_documents, 
                                  features=[Textract_Features.FORMS, Textract_Features.TABLES, Textract_Features.QUERIES], 
                                  boto3_textract_client = textract_client,
                                  queries_config=queries_config)
   
   return textract_json
   

def ask_bedrock(question):   
  prompt = f'\n\nHuman: {question}\n\nAssistant:'

  # print("prompt")
  # print(prompt)
  
  body = json.dumps({
      "prompt": prompt,
      "max_tokens_to_sample": 300,
      "temperature": 0.1,
      "top_p": 0.9,
  })

  modelId = 'anthropic.claude-v2'
  accept = 'application/json'
  contentType = 'application/json'

  response = bedrock.invoke_model(body=body, modelId=modelId, accept=accept, contentType=contentType)

  response_body = json.loads(response.get('body').read())

  # print("answer")
  # print(response_body)

  return response_body.get('completion').lstrip()

def handler(event, context):
    print("event")
    print(event)


    s3_bucket = event['Records'][0]['s3']['bucket']['name']
    print(event['Records'][0]['s3']['object']['key'])
    s3_key = urllib.parse.unquote_plus(event['Records'][0]['s3']['object']['key'])
    s3_uri_of_documents = f's3://{s3_bucket}/{s3_key}'
    print("s3_uri_of_documents=" + s3_uri_of_documents)


    query_dict = {}

    query_dict["What is the applicant full name?"] = {"col_name": "name"}
    query_dict["What is the address at the top?"] = {"col_name": "address"}
    query_dict["What is the pay period?"] = {"col_name": "pay_period"}
    query_dict["What is the pay date?"] = {"col_name": "pay_date"}
    query_dict["What is the take home pay?"] = {"col_name": "take_home_pay_amount"}
    query_dict["What is the IRD number?"] = {"col_name": "ird_number"}
    query_dict["What is the holiday pay amount?"] = {"col_name": "holiday_pay_amount"}
    query_dict["What is the number of hours for ordinary time - standard work?"] = {"col_name": "earnings_ordinary_time_standard_work_hours"}
    query_dict["What is the rate for ordinary time - standard work?"] = {"col_name": "earnings_ordinary_time_standard_work_rate_amount"}
    query_dict["What is the total for ordinary time - standard work?"] = {"col_name": "earnings_ordinary_time_standard_work_total_amount"}
    query_dict["What is the kiwisaver employee deduction percentage?"] = {"col_name": "kiwisaver_employee_deduction_percentage"}
    query_dict["What is the kiwisaver employee deduction total?"] = {"col_name": "kiwisaver_employee_deduction_total_amount"}
    query_dict["What is the total earnings?"] = {"col_name": "total_earnings_amount"}
    query_dict["What is the tax deductions amount?"] = {"col_name": "tax_deductions_amount"}
    query_dict["What is the total deductions?"] = {"col_name": "total_deductions_amount"}
    query_dict["What is the bank account the payments paid into?"] = {"col_name": "bank_account"}
    query_dict["What is the direct credit total?"] = {"col_name": "direct_credit_total_amount"}
    query_dict["What is the kiwisaver employer contribution amount?"] = {"col_name": "kiwisaver_employer_contribution_amount"}
    query_dict["how many alternative leave days in the current leave balances?"] = {"col_name": "alternative_leave_days"}

    list_of_queries = list(query_dict.keys())

    query_array = []
    for query in list_of_queries:
        query = Query(text=query)
        query_array.append(query)

    queries_config = QueriesConfig(queries=query_array)


    textract_json = do_my_custom_call_textract(s3_uri_of_documents, queries_config)


    tdoc_queries: t2.TDocument = t2.TDocumentSchema().load(textract_json) 


    json_data = {}
    json_data["employee_id"] = "123"

    page_count = 0
    for page in tdoc_queries.pages:
      page_count += 1
      query_answers = tdoc_queries.get_query_answers(page=page)

      print(f"Page {page_count} -------------------------------------------------------------")
      print(query_answers)

      for query_answer in query_answers:
        print(query_answer)

        col_name = query_dict[query_answer[0]]['col_name']
        if "pay_date" == col_name:
          json_data[col_name] = ask_bedrock(f'Extract the date from the following text in format yyyy-MM-dd : {query_answer[2]}. Only respond with the value the date and nothing else.')
          json_data["packing_slip_id"] = ask_bedrock(f'Extract the date from the following text in format yyyyMMdd : {query_answer[2]}. Only respond with the value the date and nothing else.')
        elif "kiwisaver_employee_deduction_percentage" == col_name:
          json_data[col_name] = ask_bedrock(f'Extract the number from the following text : {query_answer[2]}. Only respond with the value the number and nothing else.')
        elif "pay_period" == col_name:
          json_data[col_name + "_start"] = ask_bedrock(f'Extract the start date from the following text in format yyyy-MM-dd : {query_answer[2]}. Only respond with the value the date and nothing else.')
          json_data[col_name + "_end"] = ask_bedrock(f'Extract the end date from the following text in format yyyy-MM-dd : {query_answer[2]}. Only respond with the value the date and nothing else.')
        elif col_name.endswith("_amount"):
          json_data[col_name] = query_answer[2].lstrip('$').replace(',', '')
        else:
          json_data[col_name] = query_answer[2]

    df = None
    table_count = 0
    tdoc_tables = Document(textract_json)
    for page in tdoc_tables.pages:
      for table in page.tables:
        table_count += 1
        df = pd.DataFrame(convert_table_to_list(trp_table=table))

        rows = convert_table_to_list(table)

        # for row in rows:
        #     print(row)





    bucket_name_athena = os.environ.get('S3_BUCKET_NAME_ATHENA')
    s3_key = f'data/{s3_key}.json'
    
    
    
    json_string = json.dumps(json_data)

    print(json_string)
    
    s3_client = boto3.client('s3')
    
    try:
        # Save the JSON string to the S3 bucket
        s3_client.put_object(Bucket=bucket_name_athena, Key=s3_key, Body=json_string, ContentType='application/json')
        print(f'Successfully saved JSON to {bucket_name_athena}/{s3_key}')
        
        # Return success response
        return {
            'statusCode': 200,
            'body': json.dumps(f'Successfully saved JSON to {bucket_name_athena}/{s3_key}')
        }
    except Exception as e:
        print(f'Error saving JSON to S3: {str(e)}')
        
        # Return error response
        return {
            'statusCode': 500,
            'body': json.dumps(f'Error saving JSON to S3: {str(e)}')
        }

