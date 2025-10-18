import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import re, json, os, threading, time
from typing import Dict, Any
import discord
from discord.ext import commands
from flask import Flask, jsonify

# ===== 설정 =====
TOKEN = os.getenv("TOKEN")  # Render 환경변수에서 불러오기
TZ = ZoneInfo("Asia/Seoul")
DATA_FILE = "boss_data.json"
PRE_ALERT_MIN = 10  # 젠 10분 전 예고

# ===== 보스 리스트 (젠주기: 시간 단위) =====
BOSS_CYCLE = {
    "언두미엘": 18, "에고": 16, "아라네오": 18, "리베라": 18,
    "베나투스": 4, "비오렌트": 4, "레이디 달리아": 14,
    "장군 아쿨레우스": 22, "아멘티스": 22, "남작 브라우드모어": 24,
    "와니타스": 36, "메투스": 36, "듀플리칸": 36,
    "슈라이어": 26, "가레스": 24, "티토르": 28, "라르바": 26
}

# ===== Discord 봇 기본 설정 =====
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
SCHEDULES: Dict[int, Dict[str, Dict[str, Any]]] = {}

# ===== JSON 저장/복원 =====
def save_data():
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(SCHEDULES, f, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        print(f"[저장 오류] {e}")

def load_data():
    global SCHEDULES
    if not os.path.exists(DATA_FILE):
        print("📁 데이터 파일 없음 → 새로 생성 예정")
        SCHEDULES = {}
        return
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        SCHEDULES.clear()
        for gid, bosses in raw.items():
            gid = int(gid)
            SCHEDULES[gid] = {}
            for bname, d in bosses.items():
                try:
                    SCHEDULES[gid][bname] = {
                        "spawn": datetime.fromisoformat(d["spawn"]),
                        "kill": datetime.fromisoformat(d["kill"]),
                        "channel": d["channel"],
                        "prealert_sent": d.get("prealert_sent", False)
                    }
                except Exception as e:
                    print(f"⚠️ {bname} 데이터 손상 무시: {e}")
        print("✅ JSON 데이터 복원 완료")
    except Exception as e:
        print(f"⚠️ boss_data.json 로드 실패 → 초기화: {e}")
        SCHEDULES = {}
        save_data()

# ===== 시간 계산 =====
def parse_time(text: str):
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", text)
    if not m:
        return None
    hh, mm = int(m.group(1)), int(m.group(2))
    return (hh, mm) if 0 <= hh <= 23 and 0 <= mm <= 59 else None

def calc_spawn(boss: str, hh: int, mm: int):
    cycle = BOSS_CYCLE[boss]
    now = datetime.now(TZ)
    kill = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    spawn = kill + timedelta(hours=cycle)
    return spawn, kill, cycle

# ===== 등록 함수 =====
async def register_boss(message, boss_name, time_str):
    gid, cid = message.guild.id, message.channel.id
    parsed = parse_time(time_str)
    if not parsed:
        await message.channel.send("❌ 형식: `13:30`")
        return
    hh, mm = parsed
    spawn, kill, cycle = calc_spawn(boss_name, hh, mm)
    SCHEDULES.setdefault(gid, {})[boss_name] = {
        "spawn": spawn,
        "kill": kill,
        "channel": cid,
        "prealert_sent": False
    }
    save_data()
    await message.channel.send(
        f"✅ **{boss_name}** 등록 완료!\n🕒 {kill.strftime('%m/%d %H:%M')} → 💀 다음 젠: {spawn.strftime('%m/%d %H:%M')} ({cycle}시간)"
    )

# ===== 명령어 처리 =====
@bot.event
async def on_message(message):
    if message.author.bot:
        return

    content = message.content.strip()
    gid = message.guild.id

    if content == ".보스":
        items = SCHEDULES.get(gid, {})
        if not items:
            await message.channel.send("📭 등록된 젠이 없습니다.")
            return
        now = datetime.now(TZ)
        embed = discord.Embed(
            title="📋 보스 젠 현황",
            color=discord.Color.blurple(),
            timestamp=now
        )
        for boss, data in sorted(items.items(), key=lambda kv: kv[1]["spawn"]):
            spawn = data["spawn"]
            rem = spawn - now
            if rem.total_seconds() < 0:
                status = "⏰ 지난 젠"
                color = "🔴"
            else:
                h, m = divmod(int(rem.total_seconds() // 60), 60)
                status = f"({h}시간 {m}분 남음)"
                color = "🟩" if rem.total_seconds() > 3600 else "🟨"
            embed.add_field(
                name=f"{color} {boss}",
                value=f"{spawn.strftime('%m/%d %H:%M')} {status}",
                inline=False
            )
        embed.set_footer(text=f"기준 시각: {now.strftime('%m/%d %H:%M')}")
        await message.channel.send(embed=embed)
        return

    elif content.startswith(".삭제"):
        parts = content.split()
        if len(parts) != 2:
            await message.channel.send("❌ 사용법: `.삭제 보스이름`")
            return
        boss = parts[1]
        if gid not in SCHEDULES or boss not in SCHEDULES[gid]:
            await message.channel.send(f"📭 **{boss}** 젠 기록이 없습니다.")
            return
        del SCHEDULES[gid][boss]
        save_data()
        await message.channel.send(f"🗑️ **{boss}** 젠 기록을 삭제했습니다.")
        return

    elif content.startswith("."):
        parts = content[1:].split()
        if len(parts) != 2:
            await message.channel.send("❌ 사용법: `.보스명 13:30`")
            return
        boss_name, time_str = parts
        if boss_name not in BOSS_CYCLE:
            await message.channel.send("❌ 존재하지 않는 보스명입니다.")
            return
        await register_boss(message, boss_name, time_str)
        return

# ===== 자동 알림 루프 =====
async def alarm_loop():
    await bot.wait_until_ready()
    while not bot.is_closed():
        now = datetime.now(TZ).replace(second=0, microsecond=0)
        changed = False
        for gid, entries in list(SCHEDULES.items()):
            for boss, data in list(entries.items()):
                spawn = data["spawn"].replace(second=0, microsecond=0)
                pre = spawn - timedelta(minutes=PRE_ALERT_MIN)
                ch = bot.get_channel(data["channel"])
                if not ch:
                    continue
                if now >= pre and not data.get("prealert_sent", False) and now < spawn:
                    await ch.send(f"🔔 **{boss}** 젠 {PRE_ALERT_MIN}분 전! @everyone")
                    data["prealert_sent"] = True
                    changed = True
                if now >= spawn:
                    await ch.send(f"⚠️ **{boss} 젠 시간!** @everyone")
                    del SCHEDULES[gid][boss]
                    changed = True
        if changed:
            save_data()
        await asyncio.sleep(30)

# ===== Flask keep-alive (Render용) =====
app = Flask(__name__)

@app.route('/')
def home():
    return """
    <html>
        <head><title>BossTimerBot</title></head>
        <body style="font-family:Arial; text-align:center; margin-top:15%;">
            <h1>✅ BossTimerBot is running on Render!</h1>
            <p>Discord Bot is online and active.</p>
        </body>
    </html>
    """, 200, {"Content-Type": "text/html; charset=utf-8"}
def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, threaded=True)

# ===== Discord Bot 실행 =====
@bot.event
async def on_ready():
    print(f"✅ 로그인 완료: {bot.user}")
    load_data()
    bot.loop.create_task(alarm_loop())

def run_discord():
    bot.run(TOKEN)

# ===== 실행 순서 (Flask → Discord) =====
if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    time.sleep(3)  # Flask 감지 대기
    run_discord()
