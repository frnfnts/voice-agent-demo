"""コンテナ起動: SSM からシークレット取得 → server 起動."""
import os
from pathlib import Path


def pull_ssm_secrets():
    import boto3
    ssm = boto3.client('ssm', region_name=os.getenv('AWS_REGION', 'ap-northeast-1'))

    def get(name):
        return ssm.get_parameter(Name=name, WithDecryption=True)['Parameter']['Value']

    os.environ['OPENAI_API_KEY']        = get('openai-key')
    os.environ['RECALL_TOKEN']          = get('recallai-key')
    os.environ['GOOGLE_SA_CREDENTIALS'] = get('google-drive-credential')
    print("[startup] SSM secrets loaded", flush=True)


if not os.getenv('OPENAI_API_KEY'):
    pull_ssm_secrets()

import server
server.main()
