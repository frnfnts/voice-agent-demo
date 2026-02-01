import { google } from 'googleapis';

/**
 * Google ドキュメントの内容を文字列として取得する
 */
export async function getDocContentAsString(fileId: string, keyFileJson: string): Promise<string> {
  const serviceAccount = JSON.parse(keyFileJson);
  const auth = new google.auth.GoogleAuth({
    credentials: {
      client_email: serviceAccount.client_email,
      private_key: serviceAccount.private_key,
    },
    scopes: ['https://www.googleapis.com/auth/drive.readonly'],
  });

  const drive = google.drive({ version: 'v3', auth });

  try {
    const response = await drive.files.export({
      fileId: fileId,
      mimeType: 'text/plain',
    });

    // response.data にテキスト内容が文字列として入っています
    const content = response.data as string;

    return content;
  } catch (error) {
    console.error('テキスト取得エラー:', error);
    throw error;
  }
}

// // 実行して変数に格納する例
// (async () => {
//   // 実行例
//   const FILE_ID = '1cQSHjpoijqEkbvU8h5ZlMzk3qIdy6u4gjL4qXM4BA9w';
//   const KEY_PATH = 'ame-ai-agent.json'
//   const keyFileJson = fs.readFileSync(KEY_PATH, 'utf8');
//   const myDocText = await getDocContentAsString(FILE_ID, keyFileJson);
//   console.log('--- 取得した内容 ---');
//   console.log(myDocText); // ここで変数として利用可能
// })();
