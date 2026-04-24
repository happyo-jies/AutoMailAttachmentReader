import imaplib
import email
from email.header import decode_header, make_header
import os
import re
import getpass
import requests

# ====================== 【配置项】请在这里填写 ======================
# 邮箱账号（Outlook/Hotmail 都支持）
# 强烈建议使用环境变量而不是写死在代码里：
# Windows PowerShell 示例：
#   $env:EMAIL_USER="xxx@qq.com"
#   $env:EMAIL_PASS="你的授权码或密码"
EMAIL_USER = os.getenv("EMAIL_USER", "").strip()
EMAIL_PASS = os.getenv("EMAIL_PASS", "")
IMAP_HOST = os.getenv("IMAP_HOST", "").strip()  # 为空则根据邮箱域名自动推断

# AI 配置（这里用 豆包 API，免费好用，也可以换成 GPT）
AI_API_KEY = os.getenv("AI_API_KEY", "你的豆包API_KEY").strip()
AI_MODEL = os.getenv("AI_MODEL", "doubao-001").strip()
AI_API_URL = os.getenv("AI_API_URL", "https://api.doubao.com/v1/chat/completions").strip()

# 附件保存目录（自动创建，跨平台兼容）
SAVE_DIR = os.getenv("SAVE_DIR", "邮箱_附件").strip() or "邮箱_附件"
# =================================================================

# 创建保存目录
os.makedirs(SAVE_DIR, exist_ok=True)

def clean_filename(filename: str) -> str:
    """清理文件名，避免跨平台非法字符与路径穿越。"""
    filename = (filename or "").strip()
    filename = os.path.basename(filename)  # 防止 '..\\' / '../' 等
    filename = filename.replace("\x00", "")
    filename = re.sub(r'[\\/*?:"<>|]', "_", filename)
    filename = re.sub(r"\s+", " ", filename).strip(" .")  # Windows 不接受末尾空格/点
    if not filename:
        filename = "未命名附件"

    # Windows 保留名处理
    reserved = {
        "CON", "PRN", "AUX", "NUL",
        "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
        "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
    }
    stem, ext = os.path.splitext(filename)
    if stem.upper() in reserved:
        stem = f"_{stem}"
    filename = stem + ext

    # 长度限制（给路径留余量）
    if len(filename) > 180:
        stem, ext = os.path.splitext(filename)
        filename = stem[: 180 - len(ext)] + ext
    return filename

def decode_str(s) -> str:
    """解码邮件标题/文件名（兼容多段编码/空值）。"""
    if s is None:
        return ""
    try:
        return str(make_header(decode_header(s)))
    except Exception:
        return str(s)

def guess_imap_host(email_user: str) -> str:
    email_user = (email_user or "").strip().lower()
    domain = email_user.split("@", 1)[1] if "@" in email_user else ""
    if domain in {"outlook.com", "hotmail.com", "live.com", "msn.com"}:
        return "imap-mail.outlook.com"
    if domain in {"qq.com"}:
        return "imap.qq.com"
    if domain in {"gmail.com"}:
        return "imap.gmail.com"
    if domain in {"163.com"}:
        return "imap.163.com"
    if domain in {"126.com"}:
        return "imap.126.com"
    return ""

def ai_summary(text):
    """调用 AI 生成总结（豆包 API）"""
    if not AI_API_KEY or AI_API_KEY == "你的豆包API_KEY":
        return "AI_API_KEY 未配置，已跳过 AI 总结。"
    headers = {
        "Authorization": f"Bearer {AI_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": AI_MODEL,
        "messages": [
            {"role": "user", "content": f"请总结下面内容，输出要点：\n\n{text}"}
        ]
    }
    try:
        resp = requests.post(AI_API_URL, json=data, headers=headers, timeout=30)
        if resp.status_code >= 400:
            return f"AI 总结失败：HTTP {resp.status_code}，响应：{resp.text[:500]}"
        payload = resp.json()
        choices = payload.get("choices") or []
        if not choices:
            return f"AI 总结失败：返回无 choices，响应：{str(payload)[:500]}"
        msg = choices[0].get("message") or {}
        content = msg.get("content")
        return content if isinstance(content, str) and content.strip() else f"AI 总结失败：返回内容为空，响应：{str(payload)[:500]}"
    except Exception as e:
        return f"AI 总结失败：{str(e)}"

def read_text_file(path):
    """读取文本类附件"""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()[:10000]  # 限制长度
    except Exception as e:
        return f"无法读取文件内容（可能为非文本格式）：{str(e)}"

# ====================== 【核心：IMAP 连接 Outlook】 ======================
if not EMAIL_USER:
    raise SystemExit("请先配置 EMAIL_USER（建议用环境变量 EMAIL_USER）。")
if not EMAIL_PASS:
    EMAIL_PASS = getpass.getpass("请输入邮箱密码/授权码（输入时不可见）：")

if not IMAP_HOST:
    IMAP_HOST = guess_imap_host(EMAIL_USER)
if not IMAP_HOST:
    raise SystemExit("无法根据邮箱域名推断 IMAP_HOST，请设置环境变量 IMAP_HOST，例如 imap.qq.com / imap-mail.outlook.com。")

print(f"正在连接 IMAP：{IMAP_HOST} ...")
mail = imaplib.IMAP4_SSL(IMAP_HOST, 993)
mail.login(EMAIL_USER, EMAIL_PASS)
mail.select("INBOX")

# 搜索未读邮件
status, messages = mail.search(None, "UNSEEN")
email_ids = messages[0].split()

if not email_ids:
    print("📭 没有未读邮件")
    mail.logout()
    exit()

# 处理每一封邮件
for e_id in email_ids:
    res, msg_data = mail.fetch(e_id, "(RFC822)")
    msg = email.message_from_bytes(msg_data[0][1])
    subject = decode_str(msg["Subject"])
    print(f"\n邮件主题：{subject}")

    # 下载附件
    for part in msg.walk():
        if part.get_content_disposition() == "attachment":
            filename = decode_str(part.get_filename())
            if not filename:
                continue
            
            filename = clean_filename(filename)
            file_path = os.path.join(SAVE_DIR, filename)
            # 避免重名覆盖
            if os.path.exists(file_path):
                stem, ext = os.path.splitext(filename)
                i = 1
                while True:
                    candidate = os.path.join(SAVE_DIR, f"{stem}({i}){ext}")
                    if not os.path.exists(candidate):
                        file_path = candidate
                        filename = os.path.basename(candidate)
                        break
                    i += 1

            # 保存附件
            with open(file_path, "wb") as f:
                f.write(part.get_payload(decode=True))
            print(f"已保存附件：{filename}")

            # 读取内容 + AI 总结
            content = read_text_file(file_path)
            summary = ai_summary(content)

            # 保存总结
            summary_name = os.path.splitext(filename)[0] + "_总结.txt"
            summary_path = os.path.join(SAVE_DIR, summary_name)
            with open(summary_path, "w", encoding="utf-8") as f:
                f.write(f"邮件：{subject}\n附件：{filename}\n\n总结：\n{summary}")
            print(f"总结完成：{summary_name}")

    # 处理完成后标记为已读，避免重复处理
    try:
        mail.store(e_id, "+FLAGS", "\\Seen")
    except Exception:
        pass

mail.logout()
print("\n全部处理完成！")