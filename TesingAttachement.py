import imaplib
import email
from email.header import decode_header
from email.utils import getaddresses
from email.message import Message
import socket
import os
from pathlib import Path
import calendar
import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# ====================== 【请在这里修改你的配置】 ======================
# 邮箱配置（支持所有开启IMAP的邮箱）
EMAIL_USER = "caiqjs41848"
EMAIL_PASSWORD = "caiwoyi2026.22"  # 大部分邮箱需要用授权码，不是登录密码
IMAP_SERVER = "mail.hundsun.com"  # 下方有对照表

# 搜索配置
TARGET_SUBJECT = "问题"  # 默认：要匹配的邮件主题关键词（运行时可输入覆盖）
SAVE_PATH = r"D:\邮箱附件"  # Windows 保存路径
# SAVE_PATH = "/Users/你的用户名/Desktop/邮箱附件"  # Mac 保存路径

# =====================================================================

def clean_subject(subject):
    """清理邮件主题，解决编码问题"""
    if not subject:
        return ""
    decoded = decode_header(subject)[0]
    if decoded[1]:
        return decoded[0].decode(decoded[1])
    return str(decoded[0])

def clean_header(value: str | None) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    out: list[str] = []
    for b, enc in parts:
        if isinstance(b, bytes):
            out.append(b.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(str(b))
    return "".join(out)

def _addresses_text(value: str | None) -> str:
    # 统一成小写文本，便于 contains 匹配（包含 name 与 addr）
    decoded = clean_header(value)
    pairs = getaddresses([decoded]) if decoded else []
    tokens: list[str] = []
    for name, addr in pairs:
        name = (name or "").strip()
        addr = (addr or "").strip()
        if name:
            tokens.append(name)
        if addr:
            tokens.append(addr)
    return " ".join(tokens).lower()

def _match_message(
    msg: Message,
    subject_kw: str,
    from_kw: str,
    to_kw: str,
    cc_kw: str,
) -> bool:
    if subject_kw:
        subject = clean_subject(msg.get("Subject"))
        if subject_kw not in subject:
            return False

    if from_kw:
        if from_kw.lower() not in _addresses_text(msg.get("From")):
            return False

    if to_kw:
        if to_kw.lower() not in _addresses_text(msg.get("To")):
            return False

    if cc_kw:
        if cc_kw.lower() not in _addresses_text(msg.get("Cc")):
            return False

    return True

def is_valid_imap_server(server):
    try:
        socket.gethostbyname(server)
        return True
    except socket.gaierror:
        return False

def _imap_date(d: dt.date) -> str:
    # IMAP日期格式：DD-Mon-YYYY（Mon 必须英文缩写）
    return f"{d.day:02d}-{calendar.month_abbr[d.month]}-{d.year}"

def _safe_save_path(save_dir: str, filename: str) -> str:
    base, ext = os.path.splitext(filename)
    candidate = os.path.join(save_dir, filename)
    if not os.path.exists(candidate):
        return candidate

    i = 1
    while True:
        candidate = os.path.join(save_dir, f"{base}({i}){ext}")
        if not os.path.exists(candidate):
            return candidate
        i += 1

def _download_for_day(
    day: dt.date,
    subject_kw: str,
    from_kw: str,
    to_kw: str,
    cc_kw: str,
    save_dir: str,
    print_lock: threading.Lock,
) -> int:
    """按天搜索并下载附件。每个线程使用独立 IMAP 连接。"""
    mail = imaplib.IMAP4_SSL(IMAP_SERVER)
    try:
        mail.login(EMAIL_USER, EMAIL_PASSWORD)
        mail.select("INBOX")

        since = _imap_date(day)
        before = _imap_date(day + dt.timedelta(days=1))
        status, messages = mail.search(None, "SINCE", since, "BEFORE", before)
        mail_ids = messages[0].split() if messages and messages[0] else []

        downloaded = 0
        for mail_id in mail_ids:
            _, msg_data = mail.fetch(mail_id, "(RFC822)")
            for response_part in msg_data:
                if not isinstance(response_part, tuple):
                    continue

                msg = email.message_from_bytes(response_part[1])
                if not _match_message(msg, subject_kw, from_kw, to_kw, cc_kw):
                    continue

                with print_lock:
                    subject = clean_subject(msg.get("Subject"))
                    print(f"📩 [{day.isoformat()}] 匹配到邮件：{subject}")

                for part in msg.walk():
                    if part.get_content_maintype() == "multipart":
                        continue
                    if not part.get("Content-Disposition"):
                        continue

                    filename = part.get_filename()
                    if not filename:
                        continue

                    filename = clean_subject(filename)
                    save_path = _safe_save_path(save_dir, filename)
                    with open(save_path, "wb") as f:
                        f.write(part.get_payload(decode=True))

                    with print_lock:
                        print(f"✅ [{day.isoformat()}] 已保存：{os.path.basename(save_path)}")

                    downloaded += 1

        return downloaded
    finally:
        try:
            mail.close()
        except Exception:
            pass
        try:
            mail.logout()
        except Exception:
            pass

def _prompt_filters_days_and_path() -> tuple[str, str, str, str, int, str]:
    subject_kw = input(f"请输入要匹配的邮件主题关键词（回车使用默认：{TARGET_SUBJECT}）：").strip()
    if not subject_kw:
        subject_kw = TARGET_SUBJECT

    from_kw = input("请输入发件人匹配（邮箱/姓名关键字，可留空）：").strip()
    to_kw = input("请输入收件人(To)匹配（邮箱/姓名关键字，可留空）：").strip()
    cc_kw = input("请输入抄送(Cc)匹配（邮箱/姓名关键字，可留空）：").strip()

    days_raw = input("请输入最近天数范围 N（例如 7；回车默认 7）：").strip()
    if not days_raw:
        days = 7
    else:
        try:
            days = int(days_raw)
        except ValueError:
            print("⚠️ 最近天数必须是整数，已使用默认 7 天。")
            days = 7

        if days <= 0:
            print("⚠️ 最近天数必须 > 0，已使用默认 7 天。")
            days = 7

    save_path = input(f"请输入附件保存路径（回车使用默认：{SAVE_PATH}）：").strip()
    if not save_path:
        save_path = SAVE_PATH

    return subject_kw, from_kw, to_kw, cc_kw, days, save_path

def download_attachments():
    try:
        subject_kw, from_kw, to_kw, cc_kw, recent_days, save_dir = _prompt_filters_days_and_path()
        Path(save_dir).mkdir(parents=True, exist_ok=True)
        print(f"✅ 附件将保存到：{save_dir}")
        print(
            "🔎 匹配条件："
            f"主题='{subject_kw}'"
            + (f"，发件人包含='{from_kw}'" if from_kw else "")
            + (f"，收件人包含='{to_kw}'" if to_kw else "")
            + (f"，抄送包含='{cc_kw}'" if cc_kw else "")
            + f"；时间范围：最近 {recent_days} 天（按日期多线程提速）"
        )

        # 检查IMAP服务器配置是否正确
        if not is_valid_imap_server(IMAP_SERVER):
            print(f"\n❌ 错误：无法解析 IMAP_SERVER '{IMAP_SERVER}'，请检查服务器地址是否填写正确。")
            return

        today = dt.date.today()
        days = [
            today - dt.timedelta(days=offset)
            for offset in range(recent_days - 1, -1, -1)
        ]

        max_workers = min(4, max(1, len(days)))
        print_lock = threading.Lock()

        downloaded_total = 0
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {
                ex.submit(_download_for_day, day, subject_kw, from_kw, to_kw, cc_kw, save_dir, print_lock): day
                for day in days
            }
            for fut in as_completed(futures):
                downloaded_total += fut.result()

        print(f"\n📦 本次共下载了 {downloaded_total} 个附件。")
        print("🎉 所有任务完成！")

    except Exception as e:
        print(f"\n❌ 错误：{str(e)}")
        if isinstance(e, socket.gaierror):
            print("请检查 IMAP_SERVER 配置，确认该域名可以在本地解析。")

if __name__ == "__main__":
    download_attachments()