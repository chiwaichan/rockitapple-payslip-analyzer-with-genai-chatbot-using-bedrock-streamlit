import os, boto3
from dotenv import load_dotenv
import yaml
from langchain.prompts.few_shot import FewShotPromptTemplate
from langchain.prompts.prompt import PromptTemplate
from langchain.sql_database import SQLDatabase
from langchain.chains.sql_database.prompt import PROMPT_SUFFIX, _postgres_prompt
from langchain.embeddings.huggingface import HuggingFaceEmbeddings
from langchain.llms import Bedrock
from langchain.prompts.example_selector.semantic_similarity import (
    SemanticSimilarityExampleSelector,
)
from langchain_community.vectorstores import Chroma
from langchain_experimental.sql import SQLDatabaseChain
import streamlit as st
import io
import sys
import logging

from botocore import session
import requests
import os
from sqlalchemy.engine import create_engine

# Configure logging
logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Example usage
logger.info("This is an info log message")
logger.error("This is an error log message")



# Create an Athena client
athena = boto3.client('athena')

# List all the Athena tables
def list_athena_tables():
    try:
        # Get a list of all the databases
        databases = athena.list_databases()['DatabaseList']
        
        # print("databases")
        # print(databases)
        # Iterate through each database and list the tables
        for database in databases:
            response = athena.list_table_metadata(
                DatabaseName=database
            )
            
            # Print the table names
            # for table in response['TableMetadataList']:
            #     print(f"Database: {database}, Table: {table['Name']}")
    
    except Exception as e:
        print(f"Error: {e}")







class StreamToTextArea(io.StringIO):
    def __init__(self):
        super().__init__()
        self.text_area = st.empty()

    def write(self, message):
        self.text_area.text_area("Logs", self.getvalue())
        super().write(message)

log_stream = StreamToTextArea()
sys.stdout = log_stream

# Loading environment variables
load_dotenv()

def list_directory(path):
    try:
        # List all files and directories in the given path
        contents = os.listdir(path)
        
        # Print the contents
        for item in contents:
            raise ValueError(item)
        
        return contents

    except FileNotFoundError:
        raise ValueError(f"The directory {path} does not exist.")
        return []
    except PermissionError:
        raise ValueError(f"Permission denied to access {path}.")
        return []
    except Exception as e:
        raise ValueError(f"An unexpected error occurred: {e}")
        return []
    

credentials_path = os.path.expanduser('~')


# list_directory(credentials_path)
# Check if the credentials file exists
if not os.path.exists(credentials_path):
    raise ValueError("AWS credentials file not found." + credentials_path)

session = boto3.session.Session()
current_region = session.region_name


# configuring your instance of Amazon bedrock, selecting the CLI profile, modelID, endpoint url and region.
llm = Bedrock(
    credentials_profile_name=os.getenv("default"),
    model_id="amazon.titan-text-express-v1",
    endpoint_url=f"https://bedrock-runtime.{current_region}.amazonaws.com",
    region_name=current_region,
    verbose=True
)


# Executing the SQL database chain with the users question
def athena_answer(question):
    metadata_url = 'http://169.254.170.2/v2/metadata'

    metadata_response = requests.get(metadata_url)
    metadata = metadata_response.json()

    aws_region = metadata['AvailabilityZone'][:-1]
    athena_database = os.getenv('ATHENA_DATABASE_NAME')
    s3_bucket = os.getenv('S3_BUCKET')

    athena_conn_str = (
        f'awsathena+rest://@athena.{aws_region}.amazonaws.com:443/'
        f'{athena_database}?s3_staging_dir={s3_bucket}/athena/results/&work_group=primary'
    )






    # SQL Database Engine and preparing it to be used with Langchain sql_db_chain
    # loading the sample prompts from SampleData/payslip_examples.yaml
    engine = create_engine(athena_conn_str, echo=True)
    db = SQLDatabase(engine)
    examples = load_samples()

    print("examples")
    print(examples)

    # initiating the sql_db_chain with the specific LLM we are using, the db connection string and the selected examples
    sql_db_chain = load_few_shot_chain(llm, db, examples)
    # the answer created by Amazon Bedrock and ultimately passed back to the end user
    answer = sql_db_chain(question)
    # Passing back both the generated SQL query and the final result in a natural language format
    return answer["intermediate_steps"][1], answer["result"]


def load_samples():
    """
    Load the sql examples for few-shot prompting examples
    :return: The sql samples in from the moma_examples.yaml file
    """
    # instantiating the sql samples variable
    sql_samples = None
    # opening our prompt sample file
    with open("Sampledata/payslip_examples.yaml", "r") as stream:
        # reading our prompt samples into the sql_samples variable
        sql_samples = yaml.safe_load(stream)
    # returning the sql samples as a string
    return sql_samples


def load_few_shot_chain(llm, db, examples):
    """
    This function is used to load in the most similar prompts, format them along with the users question and then is
    passed in to Amazon Bedrock to generate an answer.
    :param llm: Large Language model you are using
    :param db: The Amazon Athena database URL
    :param examples: The samples loaded from your examples file.
    :return: The results from the SQLDatabaseChain
    """
    # This is formatting the prompts that are retrieved from the SampleData/moma_examples.yaml
    example_prompt = PromptTemplate(
        input_variables=["table_info", "input", "sql_cmd", "sql_result", "answer"],
        template=(
            "{table_info}\n\nQuestion: {input}\nSQLaQuery: {sql_cmd}\nSQLResult:"
            " {sql_result}\nAnswer: {answer}"
        ),
    )
    # instantiating the hugging face embeddings model to be used to produce embeddings of user queries and prompts
    local_embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
    # The example selector loads the examples, creates the embeddings, stores them in Chroma (vector store) and a
    # semantic search is performed to see the similarity between the question and prompts, it returns the 3 most similar
    # prompts as defined by k
    example_selector = SemanticSimilarityExampleSelector.from_examples(
        examples,
        local_embeddings,
        Chroma,
        k=min(3, len(examples)),
    )
    # This is orchestrating the example selector (finding similar prompts to the question), example_prompt (formatting
    # the retrieved prompts, and formatting the chat history and the user input
    few_shot_prompt = FewShotPromptTemplate(
        example_selector=example_selector,
        example_prompt=example_prompt,
        prefix=_postgres_prompt + "Provide no preamble",
        suffix=PROMPT_SUFFIX,
        input_variables=["table_info", "input", "top_k"],
    )
    # Where the LLM, DB and prompts are all orchestrated to answer a user query.
    return SQLDatabaseChain.from_llm(
        llm,
        db,
        prompt=few_shot_prompt,
        use_query_checker=True,
        verbose=True,
        return_intermediate_steps=True,
    )