import imaplib
import email
from email.header import decode_header
import socket
import os
from pathlib import Path
import calendar
import datetime as dt

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

def is_valid_imap_server(server):
    try:
        socket.gethostbyname(server)
        return True
    except socket.gaierror:
        return False

def _imap_date(d: dt.date) -> str:
    # IMAP日期格式：DD-Mon-YYYY（Mon 必须英文缩写）
    return f"{d.day:02d}-{calendar.month_abbr[d.month]}-{d.year}"

def _prompt_subject_and_days() -> tuple[str, int]:
    subject = input(f"请输入要匹配的邮件主题关键词（回车使用默认：{TARGET_SUBJECT}）：").strip()
    if not subject:
        subject = TARGET_SUBJECT

    days_raw = input("请输入最近天数范围 N（例如 7；回车默认 7）：").strip()
    if not days_raw:
        return subject, 7

    try:
        days = int(days_raw)
    except ValueError:
        print("⚠️ 最近天数必须是整数，已使用默认 7 天。")
        return subject, 7

    if days <= 0:
        print("⚠️ 最近天数必须 > 0，已使用默认 7 天。")
        return subject, 7

    return subject, days

def download_attachments():
    # 自动创建保存目录
    Path(SAVE_PATH).mkdir(parents=True, exist_ok=True)
    print(f"✅ 附件将保存到：{SAVE_PATH}")

    try:
        target_subject, recent_days = _prompt_subject_and_days()
        print(f"🔎 主题关键词：{target_subject}；时间范围：最近 {recent_days} 天")
        downloaded_count = 0

        # 检查IMAP服务器配置是否正确
        if not is_valid_imap_server(IMAP_SERVER):
            print(f"\n❌ 错误：无法解析 IMAP_SERVER '{IMAP_SERVER}'，请检查服务器地址是否填写正确。")
            return

        # 1. 连接IMAP服务器（SSL加密，通用）
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_USER, EMAIL_PASSWORD)
        print("✅ 邮箱登录成功")

        # 2. 选择收件箱
        mail.select("INBOX")

        # 3. 只搜索最近 N 天的邮件
        today = dt.date.today()
        since = _imap_date(today - dt.timedelta(days=recent_days))
        before = _imap_date(today + dt.timedelta(days=1))  # BEFORE 是“严格早于”，所以用明天包含今天

        status, messages = mail.search(None, "SINCE", since, "BEFORE", before)
        mail_ids = messages[0].split()

        if not mail_ids:
            print("ℹ️ 收件箱中没有邮件")
            return

        print(f"ℹ️ 共找到 {len(mail_ids)} 封邮件，开始筛选主题...")

        # 4. 遍历邮件
        for mail_id in mail_ids:
            res, msg_data = mail.fetch(mail_id, "(RFC822)")
            for response_part in msg_data:
                if not isinstance(response_part, tuple):
                    continue

                # 解析邮件
                msg = email.message_from_bytes(response_part[1])
                subject = clean_subject(msg["Subject"])

                # 筛选目标主题
                if target_subject not in subject:
                    continue

                print(f"\n📩 匹配到邮件：{subject}")

                # 5. 下载附件
                for part in msg.walk():
                    if part.get_content_maintype() == "multipart":
                        continue
                    if not part.get("Content-Disposition"):
                        continue

                    # 获取文件名
                    filename = part.get_filename()
                    if not filename:
                        continue

                    # 解码文件名（解决中文乱码）
                    filename = clean_subject(filename)
                    save_path = os.path.join(SAVE_PATH, filename)

                    # 保存文件
                    with open(save_path, "wb") as f:
                        f.write(part.get_payload(decode=True))

                    print(f"✅ 已保存：{filename}")
                    downloaded_count += 1

        mail.close()
        mail.logout()
        print(f"\n📦 本次共下载了 {downloaded_count} 个附件。")
        print("🎉 所有任务完成！")

    except Exception as e:
        print(f"\n❌ 错误：{str(e)}")
        if isinstance(e, socket.gaierror):
            print("请检查 IMAP_SERVER 配置，确认该域名可以在本地解析。")

if __name__ == "__main__":
    download_attachments()