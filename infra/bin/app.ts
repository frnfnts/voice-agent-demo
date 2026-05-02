import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { ServerStack } from '../lib/server-stack';
import { AdminStack }  from '../lib/admin-stack';

const app = new cdk.App();
const env = { account: process.env.CDK_DEFAULT_ACCOUNT ?? '918956680623', region: 'ap-northeast-1' };

const server = new ServerStack(app, 'AiInterviewerServer', { env });

const admin = new AdminStack(app, 'AiInterviewerAdmin', {
  env,
  frontendUrl: 'https://develop.d1bjfvyjny35cg.amplifyapp.com',
  serverUrl:   server.serverUrl,
});
admin.addDependency(server);
