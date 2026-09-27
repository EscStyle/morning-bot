import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
from supabase import create_client, Client
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

# --- ตั้งค่า Supabase และ Token บอทกะเช้า ---
SUPABASE_URL = "https://gxqztvcwamchihnqplin.supabase.co"
SUPABASE_KEY = "sb_publishable_lnQnwygZZvi6orL46p9okA_gO0irVDQ"
TOKEN = "8882935399:AAENBHOdga_6B6Zlu_AFhqVRQtF-OB7ilzQ"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

GROUP_CHAT_ID = None
BREAK_TYPES = {"ห้องน้ำ": 15, "ดูดบุหรี่": 10, "กินข้าว": 30, "ซื้อของ": 30}
RETURN_COMMANDS = ["กลับ", "มาค่ะ", "มาครับ", "เข้า"]

# --- ฟังก์ชันจำลอง Web Server สำหรับ Render (ฟรีแพลน) ---
def run_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    class SimpleHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bot is alive!")
        def log_message(self, format, *args):
            pass # ปิด Log HTTP ยุ่บยั่บ
            
    server = HTTPServer(("0.0.0.0", port), SimpleHandler)
    server.serve_forever()

def get_work_date():
    """คำนวณรอบวันของกะเช้า (ใช้วันปัจจุบันตามปฏิทิน)"""
    return datetime.now().strftime("%Y-%m-%d")

def get_or_create_employee(emp_id, work_date):
    res = supabase.table("employee_data").select("*").eq("emp_id", emp_id).eq("work_date", work_date).execute()
    if not res.data:
        new_data = {
            "emp_id": emp_id, 
            "work_date": work_date, 
            "shift": "กะเช้า (05:00 - 18:00 น.)", 
            "quota_total": 60, 
            "quota_used": 0, 
            "meal_used": 0, 
            "meal_total": 1
        }
        supabase.table("employee_data").insert(new_data).execute()
        return new_data
    return res.data[0]

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global GROUP_CHAT_ID
    if not update.message or not update.message.text: 
        return
    GROUP_CHAT_ID = update.message.chat_id
    text = update.message.text.strip()
    current_date = get_work_date()

    # 1. พิมพ์คำว่า "สรุป" เพื่อดูรายงานของทุกคน
    if text == "สรุป":
        summary_text = f"📊 **สรุปยอดกะเช้า ({current_date})**\n----------------------------------------\n"
        emp_res = supabase.table("employee_data").select("*").eq("work_date", current_date).execute()
        active_res = supabase.table("active_breaks").select("emp_id, break_type").execute()
        active_dict = {r["emp_id"]: r["break_type"] for r in active_res.data}
        
        if not emp_res.data:
            summary_text += "❌ ยังไม่มีข้อมูลการเบรค"
        else:
            for d in emp_res.data:
                status = f"⏳ กำลังเบรค ({active_dict[d['emp_id']]})" if d['emp_id'] in active_dict else "🟢 ทำงานปกติ"
                summary_text += (
                    f"👤 รหัส: `{d['emp_id']}` | {status}\n"
                    f"• ใช้ไป: {d['quota_used']}/60 นาที | ข้าว: {d['meal_used']}/1\n"
                    f"----------------------------------------\n"
                )
        await update.message.reply_text(summary_text, parse_mode="Markdown")
        return

    parts = text.split()
    if len(parts) < 2: 
        return
    emp_id, action = parts[0], parts[1]
    data = get_or_create_employee(emp_id, current_date)
    quota_left = data["quota_total"] - data["quota_used"]

    # 2. ขอสรุปเฉพาะบุคคล (เช่น "01 สรุป")
    if action == "สรุป":
        active_res = supabase.table("active_breaks").select("break_type").eq("emp_id", emp_id).execute()
        status = f"⏳ กำลังเบรค ({active_res.data[0]['break_type']})" if active_res.data else "🟢 ทำงานปกติ"
        await update.message.reply_text(
            f"📊 รหัส `{emp_id}`\nสถานะ: {status}\n⏳ โควตาเหลือ: {quota_left} นาที\n🍽️ กินข้าว: {data['meal_used']}/1 ครั้ง", 
            parse_mode="Markdown"
        )
        return

    # 3. แจ้งกลับเข้าทำงาน (เช่น "01 กลับ")
    if action in RETURN_COMMANDS:
        active_res = supabase.table("active_breaks").select("*").eq("emp_id", emp_id).execute()
        if not active_res.data:
            await update.message.reply_text(f"รหัส {emp_id} ยังไม่ได้เริ่มเบรค")
            return
        info = active_res.data[0]
        start_time = datetime.fromisoformat(info["start_time"])
        elapsed = max(1, int((datetime.now() - start_time).total_seconds() // 60))
        new_used = data["quota_used"] + elapsed
        
        supabase.table("employee_data").update({"quota_used": new_used}).eq("emp_id", emp_id).eq("work_date", current_date).execute()
        supabase.table("break_history").insert({
            "emp_id": emp_id, 
            "work_date": current_date, 
            "break_type": info["break_type"], 
            "used_mins": elapsed, 
            "time_range": f"{start_time.strftime('%H:%M')} - {datetime.now().strftime('%H:%M')}"
        }).execute()
        supabase.table("active_breaks").delete().eq("emp_id", emp_id).execute()
        
        await update.message.reply_text(f"🏁 รหัส {emp_id} กลับมาแล้ว ใช้เวลา {elapsed} นาที (เหลือโควตา {60 - new_used} นาที)")
        return

    # 4. เริ่มเบรค (เช่น "01 ห้องน้ำ", "01 กินข้าว")
    if action in BREAK_TYPES:
        active_res = supabase.table("active_breaks").select("*").eq("emp_id", emp_id).execute()
        if active_res.data:
            await update.message.reply_text(f"รหัส {emp_id} กำลังเบรคอยู่")
            return
        dur = BREAK_TYPES[action]
        meal_used = data["meal_used"]
        if action in ["กินข้าว", "ซื้อของ"]:
            if meal_used >= 1:
                await update.message.reply_text(f"❌ ใช้สิทธิ์ข้าวครบ 1 ครั้งแล้วสำหรับกะเช้า")
                return
            meal_used += 1
            supabase.table("employee_data").update({"meal_used": meal_used}).eq("emp_id", emp_id).eq("work_date", current_date).execute()
        
        supabase.table("active_breaks").upsert({
            "emp_id": emp_id, 
            "start_time": datetime.now().isoformat(), 
            "allowed_mins": dur, 
            "break_type": action
        }).execute()
        
        await update.message.reply_text(f"⏳ รหัส {emp_id} เริ่มเบรค {action} ({dur} นาที)")

if __name__ == '__main__':
    # เปิด Web Server จำลองในเบื้องหลังเพื่อให้ Render ผ่านการเช็ค Port และรันฟรีได้
    server_thread = threading.Thread(target=run_dummy_server, daemon=True)
    server_thread.start()

    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Morning Shift Bot is running...")
    
    app.run_polling()
