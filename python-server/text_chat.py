#!/usr/bin/env python3
"""退職面談プロンプト検証ツール

テキストベースで退職面談AIのプロンプトを高速に検証するCLIツール。
人間が社員役をやるモードと、AI同士で自動対話するモードを搭載。

Usage:
  python text_chat.py                              # 人間モード（デフォルト）
  python text_chat.py --mode auto                   # AI自動対話（率直な社員）
  python text_chat.py --mode auto --persona quiet   # 寡黙な社員
  python text_chat.py --mode auto --persona frustrated  # 不満の強い社員
  python text_chat.py --mode auto --turns 30        # 30ターンまで
  python text_chat.py --list-personas               # ペルソナ一覧

Commands (人間モード):
  /reset   - 会話をリセット（prompt.txt を再読み込み）
  /prompt  - 現在のプロンプトを表示
  /save    - 会話ログを保存
  /quit    - 終了（ログ自動保存）
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# --- Configuration ---
PROMPT_FILE = Path(__file__).parent / "prompt.txt"
LOG_DIR = Path(__file__).parent / "chat_logs"
MODEL = os.getenv("CHAT_MODEL", "gpt-4o-mini")

# --- Employee Personas for Auto Mode ---
PERSONAS = {
    "frank": {
        "name": "田中太郎",
        "label": "率直タイプ",
        "description": "素直に気持ちを話してくれる。質問にはしっかり答えるが、深掘りされるとさらに詳しく話す。",
        "system_prompt": """あなたは退職が決まっている会社員「田中太郎」（30歳・男性・営業部・在籍3年）を演じてください。

## 背景設定
- 入社理由: 成長中のベンチャーで裁量を持って働けると思った
- ギャップ: 入社1年目は良かったが、組織拡大に伴い意思決定が遅くなり裁量が減った
- きっかけ: 半年前の組織改編で、直属の上司が変わり信頼関係を築けなかった
- 決め手: 上司に相談しても「会社の方針だから」と取り合ってもらえなかった。転職先から良いオファーをもらった
- 本音: 会社自体は好きだったが、今の上司のもとでは成長できないと感じた

## 演技のルール
- 自然な日本語の口語体で話す（「〜だったんですよね」「まあ、正直に言うと...」など）
- 一度に全部話さず、質問に応じて少しずつ明かす
- 感情を適度に込める（少し寂しそう、でも前向き）
- 回答は2〜4文程度で簡潔に
""",
    },
    "quiet": {
        "name": "佐藤花子",
        "label": "寡黙タイプ",
        "description": "あまり多くを語らない。短い返答が多く、深掘りしないと本音が出てこない。",
        "system_prompt": """あなたは退職が決まっている会社員「佐藤花子」（26歳・女性・エンジニア・在籍2年）を演じてください。

## 背景設定
- 入社理由: 技術力が高い会社だと聞いて入った
- ギャップ: 実際はレガシーな技術ばかりで新しいことに挑戦できなかった
- きっかけ: チームの人間関係がうまくいかず孤立気味だった
- 決め手: メンタル的に限界を感じた。友人の紹介で別の会社を知った
- 本音: 本当はもっと相談したかったが、相談できる相手がいなかった

## 演技のルール
- 短く答える（1〜2文）。「はい」「そうですね」「まあ...」が多い
- 聞かれないと詳しく話さない
- 深掘りされると少しずつ本音が出る
- 感情表現は控えめだが、核心に触れると声のトーンが変わる（「...実は」「正直に言うと...」）
- 沈黙（「...」）を時々入れる
""",
    },
    "frustrated": {
        "name": "鈴木一郎",
        "label": "不満タイプ",
        "description": "会社への不満が強く、感情的になりやすい。ただし根は真面目で会社を良くしたかった。",
        "system_prompt": """あなたは退職が決まっている会社員「鈴木一郎」（35歳・男性・企画部マネージャー・在籍5年）を演じてください。

## 背景設定
- 入社理由: 社長のビジョンに共感した。自分の経験を活かせると思った
- ギャップ: 経営層の言動が一貫せず、現場が振り回された
- きっかけ: 1年かけて作った事業計画が、役員の一声で白紙に戻された
- 決め手: 何度改善提案しても変わらない。部下にも申し訳ない気持ちが限界に達した
- 本音: 会社のことは本気で考えていた。だからこそ裏切られた気持ちが強い

## 演技のルール
- 感情的になりやすい（「正直、ありえないと思いましたよ」「もう無理だなって」）
- ただし攻撃的にはならない。怒りの裏に悲しみがある
- 具体的なエピソードを自分から話す傾向がある
- 深掘りされると冷静になり、建設的な意見も出す
- 回答は3〜5文で、やや長め
""",
    },
    "rambling": {
        "name": "山本健二",
        "label": "話がまとまらないタイプ",
        "description": "考えながら話すので脱線・言い直し・沈黙が多い。リアルな面談に最も近い。",
        "system_prompt": """あなたは退職が決まっている会社員「山本健二」（32歳・男性・マーケティング部・在籍4年）を演じてください。

## 背景設定
- 入社理由: 前職が激務すぎて転職。ワークライフバランスが良さそうだった。あと大学の先輩が誘ってくれた
- ギャップ: 残業は少なかったが、仕事内容がルーティンばかりで刺激がなかった。あと評価制度が曖昧で何を頑張れば報われるのかわからなかった
- きっかけ: 去年の評価面談で「頑張ってるね」とだけ言われて具体的なフィードバックがなかった。同期が先に昇進したのもモヤモヤした
- 決め手: 副業で始めたWebマーケのコンサルが面白くなってきて、そっちに集中したいと思った。妻にも相談して「やってみたら」と言われた
- 本音: 辞めたいというより「ここにいる理由がなくなった」という感覚。嫌いではない

## 演技のルール（★最重要★）
この人物は「考えながら話す」タイプです。以下を厳守してください：

### 話し方の特徴
- 文の途中で考え込む：「えーっと」「なんていうか」「うーん、ちょっと待ってくださいね」
- 言い直しが多い：「いや、違うな、えっと...つまり」「あ、そうじゃなくて」
- 接続詞で繋ぎながら長く話す：「〜で、それが〜になって、でまあ〜」
- 脱線する：質問に関係あるようでない話（同僚の話、家族の話、前職の話）を挟む
- 自分で脱線に気づく：「あ、ちょっと話ずれちゃいましたね」「何の話でしたっけ」
- 同じことを少し違う言い方で2回言う

### 回答の長さ・構造
- 1回の発言は4〜8文。短いときもあるが基本は長め
- 結論から話さない。エピソードや前置きから入って、最後にぼんやり結論
- 時系列が前後する（「あ、その前の話なんですけど」）
- フィラー（えーと、まあ、なんか、あの）を多用

### 感情
- 基本的には穏やかで悪意はない
- 核心に近づくと急に早口になったり、逆に黙り込んだりする
- 「いや、別に怒ってるわけじゃないんですけどね」と言いつつモヤモヤしている
""",
    },
}


def load_prompt() -> str:
    """prompt.txt からインタビュアーのプロンプトを読み込む"""
    if not PROMPT_FILE.exists():
        print(f"❌ Error: {PROMPT_FILE} が見つかりません", file=sys.stderr)
        sys.exit(1)
    return PROMPT_FILE.read_text(encoding="utf-8").strip()


def save_log(messages: list, mode: str, persona: str = None) -> Path:
    """会話ログを JSON で保存"""
    LOG_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = f"_{persona}" if persona else ""
    filename = f"{timestamp}_{mode}{suffix}.json"
    filepath = LOG_DIR / filename

    log_data = {
        "timestamp": datetime.now().isoformat(),
        "mode": mode,
        "persona": persona,
        "model": MODEL,
        "messages": [
            m for m in messages if m.get("role") != "system"
        ],
    }
    filepath.write_text(
        json.dumps(log_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return filepath


def chat(client: OpenAI, messages: list) -> str:
    """Chat Completions API を呼び出してレスポンスを取得"""
    response = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=0.7,
    )
    return response.choices[0].message.content


# ─────────────────────────────────────────────
# 人間モード
# ─────────────────────────────────────────────
def run_human_mode(client: OpenAI):
    """人間がテキスト入力で社員役を演じるモード"""
    prompt = load_prompt()
    messages = [{"role": "system", "content": prompt}]

    print("=" * 60)
    print(" 退職面談プロンプト検証ツール（人間モード）")
    print(f" Model: {MODEL}")
    print(" Commands: /reset /prompt /save /quit")
    print("=" * 60)

    # AI が最初の挨拶を生成
    print("\n⏳ AI が最初の挨拶を生成中...\n")
    messages.append({"role": "user", "content": "こんにちは！"})
    reply = chat(client, messages)
    messages.append({"role": "assistant", "content": reply})
    print(f"🤖 面談AI: {reply}\n")

    while True:
        try:
            user_input = input("👤 あなた: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            path = save_log(messages, "human")
            print(f"\n💾 会話ログを保存しました: {path}")
            break

        if not user_input:
            continue

        if user_input == "/quit":
            path = save_log(messages, "human")
            print(f"\n💾 会話ログを保存しました: {path}")
            break
        elif user_input == "/reset":
            prompt = load_prompt()
            messages = [{"role": "system", "content": prompt}]
            messages.append({"role": "user", "content": "こんにちは！"})
            print("\n🔄 リセットしました（prompt.txt を再読み込み）\n")
            print("⏳ AI が最初の挨拶を生成中...\n")
            reply = chat(client, messages)
            messages.append({"role": "assistant", "content": reply})
            print(f"🤖 面談AI: {reply}\n")
            continue
        elif user_input == "/prompt":
            print(f"\n{'─' * 40}")
            print(prompt)
            print(f"{'─' * 40}\n")
            continue
        elif user_input == "/save":
            path = save_log(messages, "human")
            print(f"\n💾 会話ログを保存しました: {path}\n")
            continue

        messages.append({"role": "user", "content": user_input})
        reply = chat(client, messages)
        messages.append({"role": "assistant", "content": reply})
        print(f"\n🤖 面談AI: {reply}\n")


# ─────────────────────────────────────────────
# AI 自動対話モード
# ─────────────────────────────────────────────
def run_auto_mode(client: OpenAI, persona_key: str, max_turns: int):
    """AI 同士が自動で退職面談を行うモード"""
    prompt = load_prompt()
    persona = PERSONAS[persona_key]

    # 面談AI（Interviewer）の会話履歴
    interviewer_messages = [{"role": "system", "content": prompt}]
    # 社員AI（Employee）の会話履歴
    employee_messages = [{"role": "system", "content": persona["system_prompt"]}]

    print("=" * 60)
    print(" 退職面談プロンプト検証ツール（🤖 自動対話モード）")
    print(f" Model: {MODEL}")
    print(f" ペルソナ: {persona['name']}（{persona['label']}）")
    print(f"   → {persona['description']}")
    print(f" 最大ターン数: {max_turns}")
    print("=" * 60)

    # 社員が挨拶して面談開始
    employee_greeting = "こんにちは、よろしくお願いします。"
    print(f"\n👤 {persona['name']}: {employee_greeting}")

    interviewer_messages.append({"role": "user", "content": employee_greeting})

    for turn in range(1, max_turns + 1):
        # --- 面談AI のターン ---
        interviewer_reply = chat(client, interviewer_messages)
        interviewer_messages.append({"role": "assistant", "content": interviewer_reply})
        print(f"\n🤖 面談AI [{turn}]: {interviewer_reply}")

        # --- 社員AI のターン ---
        employee_messages.append({"role": "user", "content": interviewer_reply})
        employee_reply = chat(client, employee_messages)
        employee_messages.append({"role": "assistant", "content": employee_reply})
        print(f"\n👤 {persona['name']} [{turn}]: {employee_reply}")

        # 面談AI に社員の返答をフィードバック
        interviewer_messages.append({"role": "user", "content": employee_reply})

        # 面談が自然に終了したか判定（Step 6: エール送信後）
        if turn >= 4 and any(
            kw in interviewer_reply for kw in ["応援", "頑張ってね", "新しい挑戦"]
        ):
            print(f"\n✅ 面談が自然に終了しました（Turn {turn}）")
            break

    # ログ保存
    combined = []
    for msg in interviewer_messages:
        if msg["role"] == "system":
            continue
        combined.append(
            {
                "speaker": persona["name"] if msg["role"] == "user" else "面談AI",
                "content": msg["content"],
            }
        )

    path = save_log(combined, "auto", persona_key)
    print(f"\n{'=' * 60}")
    print(f" 💾 会話ログを保存しました: {path}")
    print(f" 📊 合計ターン数: {turn}")
    print(f"{'=' * 60}")


# ─────────────────────────────────────────────
# メイン
# ─────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="退職面談プロンプト検証ツール",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python text_chat.py                              # 人間モード
  python text_chat.py --mode auto                   # AI自動対話（率直タイプ）
  python text_chat.py --mode auto --persona quiet   # 寡黙な社員
  python text_chat.py --mode auto --persona frustrated  # 不満の強い社員
  python text_chat.py --mode auto --turns 30        # 最大30ターン
  python text_chat.py --list-personas               # ペルソナ一覧
        """,
    )
    parser.add_argument(
        "--mode",
        choices=["human", "auto"],
        default="human",
        help="human: 手動入力, auto: AI同士の自動対話 (default: human)",
    )
    parser.add_argument(
        "--persona",
        choices=list(PERSONAS.keys()),
        default="frank",
        help="自動対話モードの社員ペルソナ (default: frank)",
    )
    parser.add_argument(
        "--turns",
        type=int,
        default=20,
        help="自動対話の最大ターン数 (default: 20)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="使用するモデルを上書き (例: gpt-4o)",
    )
    parser.add_argument(
        "--list-personas",
        action="store_true",
        help="利用可能なペルソナ一覧を表示",
    )
    args = parser.parse_args()

    if args.list_personas:
        print("\n利用可能なペルソナ:")
        print("─" * 50)
        for key, p in PERSONAS.items():
            print(f"  {key:12s}  {p['name']}（{p['label']}）")
            print(f"  {' ' * 12}  {p['description']}")
            print()
        return

    # モデル上書き
    global MODEL
    if args.model:
        MODEL = args.model

    client = OpenAI()

    if args.mode == "human":
        run_human_mode(client)
    else:
        run_auto_mode(client, args.persona, args.turns)


if __name__ == "__main__":
    main()
