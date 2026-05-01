"""管理 UI Lambda: Recall.ai ボットを会議に参加させる独立サービス."""
import json
import os
import urllib.request
import urllib.error

RECALL_API_BASE = os.environ['RECALL_API_BASE']
FRONTEND_URL    = os.environ['FRONTEND_URL']
SERVER_URL      = os.environ['SERVER_URL']

# SSM からの取得はコールドスタート時のみ (グローバルキャッシュ)
_RECALL_TOKEN = None
_ADMIN_TOKEN  = None


def _ssm_get(name: str) -> str:
    import boto3
    ssm = boto3.client('ssm', region_name=os.environ.get('AWS_PARAM_REGION', 'ap-northeast-1'))
    return ssm.get_parameter(Name=name, WithDecryption=True)['Parameter']['Value']


def recall_token() -> str:
    global _RECALL_TOKEN
    if not _RECALL_TOKEN:
        _RECALL_TOKEN = _ssm_get('recallai-key')
    return _RECALL_TOKEN


def admin_token() -> str:
    global _ADMIN_TOKEN
    if not _ADMIN_TOKEN:
        _ADMIN_TOKEN = _ssm_get('admin-token')
    return _ADMIN_TOKEN


ADMIN_HTML = """<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8"><title>AI Interviewer Admin</title>
<style>
  body{font-family:sans-serif;max-width:560px;margin:40px auto;padding:0 20px}
  label{display:block;margin-top:16px;font-weight:bold}
  input,select{width:100%;padding:8px;margin-top:4px;box-sizing:border-box}
  button{margin-top:24px;padding:12px 24px;background:#2563eb;color:#fff;
         border:none;border-radius:4px;cursor:pointer;font-size:16px}
  #res{margin-top:20px;padding:12px;background:#f0f9ff;border-radius:4px;
       white-space:pre-wrap;font-family:monospace;display:none}
  #err{margin-top:20px;padding:12px;background:#fef2f2;border-radius:4px;display:none}
</style></head><body>
<h1>AI Interviewer — ボット参加</h1>
<form id="f">
  <label>ミーティング URL *
    <input type="url" name="meeting_url"
           placeholder="https://meet.google.com/xxx-xxx-xxx" required>
  </label>
  <label>シナリオ
    <select name="scenario">
      <option value="exit_interview">退職面談 (exit_interview)</option>
      <option value="compliance">コンプライアンス (compliance)</option>
      <option value="test">テスト (test)</option>
    </select>
  </label>
  <label><input type="checkbox" name="is_debug"> デバッグモード</label>
  <button type="submit">ボットを参加させる</button>
</form>
<div id="res"></div><div id="err"></div>
<script>
document.getElementById('f').addEventListener('submit', async e => {
  e.preventDefault();
  const d = new FormData(e.target);
  const res = document.getElementById('res'), err = document.getElementById('err');
  res.style.display = err.style.display = 'none';
  try {
    const r = await fetch('?token=' + new URLSearchParams(location.search).get('token'), {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        meeting_url: d.get('meeting_url'),
        scenario: d.get('scenario'),
        is_debug: d.get('is_debug') === 'on',
      }),
    });
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || r.statusText);
    res.textContent = JSON.stringify(j, null, 2);
    res.style.display = 'block';
  } catch(e) {
    err.textContent = 'Error: ' + e.message;
    err.style.display = 'block';
  }
});
</script></body></html>"""


def handler(event, context):
    method = event.get('requestContext', {}).get('http', {}).get('method', 'GET')
    query  = event.get('queryStringParameters') or {}
    token  = query.get('token', '')

    if token != admin_token():
        return {'statusCode': 401, 'body': 'Unauthorized'}

    if method == 'GET':
        return {
            'statusCode': 200,
            'headers': {'Content-Type': 'text/html; charset=utf-8'},
            'body': ADMIN_HTML,
        }

    if method == 'POST':
        try:
            body = json.loads(event.get('body') or '{}')
        except json.JSONDecodeError:
            return _json_response(400, {'error': 'Invalid JSON'})

        meeting_url = body.get('meeting_url', '').strip()
        scenario    = body.get('scenario', 'exit_interview')
        is_debug    = bool(body.get('is_debug', False))

        if not meeting_url:
            return _json_response(400, {'error': 'meeting_url is required'})

        ws_url = SERVER_URL.replace('https://', 'wss://')
        camera_url = (
            f"{FRONTEND_URL}"
            f"?wss={ws_url}"
            f"&debug={str(is_debug).lower()}"
            f"&scenario={scenario}"
        )

        payload = {
            'meeting_url': meeting_url,
            'bot_name': 'Aya',
            'output_media': {'camera': {'kind': 'webpage', 'config': {'url': camera_url}}},
            'variant': {
                'zoom': 'web_4_core',
                'google_meet': 'web_4_core',
                'microsoft_teams': 'web_4_core',
            },
            'recording_config': {'include_bot_in_recording': {'audio': True}},
        }

        req = urllib.request.Request(
            f"{RECALL_API_BASE}/bot/",
            data=json.dumps(payload).encode(),
            headers={
                'Authorization': recall_token(),
                'Content-Type': 'application/json',
                'Accept': 'application/json',
            },
            method='POST',
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            detail = json.loads(e.read())
            return _json_response(502, {'error': 'Recall.ai error', 'detail': detail})

        bot_id = data.get('id')
        if bot_id:
            _notify_server(bot_id)

        return _json_response(200, {'status': 'ok', 'bot_id': bot_id})

    return _json_response(405, {'error': 'Method not allowed'})


def _notify_server(bot_id: str):
    """Python サーバーの /register-bot に bot_id を登録 (ベストエフォート)."""
    try:
        req = urllib.request.Request(
            f"{SERVER_URL}/register-bot",
            data=json.dumps({'bot_id': bot_id}).encode(),
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


def _json_response(status: int, body: dict) -> dict:
    return {
        'statusCode': status,
        'headers': {'Content-Type': 'application/json'},
        'body': json.dumps(body, ensure_ascii=False),
    }
