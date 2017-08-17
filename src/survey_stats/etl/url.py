import io
import zipfile
import pandas as pd
import urllib.request
import multiprocessing
from retry import retry
import boto3
from botocore import UNSIGNED
from botocore.client import Config

from survey_stats import log

logger = log.getLogger(__name__)

s3 = boto3.client('s3', config=Config(signature_version=UNSIGNED))


def fetch_s3_bytes(url):
    bucket, key = url[5:].split('/', 1)
    logger.info('fetching s3 url', url=url, bucket=bucket, key=key)
    obj = s3.get_object(Bucket=bucket, Key=key)
    return obj['Body']


@retry(tries=5, delay=2, backoff=2, logger=logger)
def fetch_data_from_url(url):
    if os.path.isfile(url):
        return open(url,'r',errors='ignore')
    elif url.startswith('s3://'):
        return fetch_s3_bytes(url)
    else:
        return urllib.request.urlopen(url)
