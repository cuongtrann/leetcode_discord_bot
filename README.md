# LeetCode Discord Bot

Bot Discord theo dõi tiến độ LeetCode hằng ngày, tự nhắc giờ cố định, cảnh cáo cuối ngày, và đồng bộ lịch sử để sửa lệch streak khi quên `/check`.

## 1) Chuẩn bị
Tạo file `.env` tại thư mục dự án:

```env
DISCORD_TOKEN=your_discord_bot_token
```

## 2) Deploy bằng Docker (server)
Chạy đúng lệnh sau tại thư mục chứa `bot.py`, `requirements.txt`, `.env`:

```bash
docker run -d \
  --name leetcode-bot \
  --restart unless-stopped \
  --env-file .env \
  -v "$(pwd):/app" \
  -w /app \
  python:3.11-slim \
  sh -c "pip install --no-cache-dir -r requirements.txt && python bot.py"
```

Kiểm tra trạng thái:

```bash
docker ps
docker logs -f leetcode-bot
```

Dừng / chạy lại:

```bash
docker stop leetcode-bot
docker start leetcode-bot
```

## 3) Lệnh Discord chính
- `/setup <channel>`: chọn kênh thông báo (admin).
- `/register <leetcode_username>`: đăng ký tài khoản LeetCode.
- `/check`: verify bài đã làm hôm nay.
- `/status`: xem tiến độ nhóm.
- `/sync_history days:<1-90>`: đồng bộ lịch sử gần đây từ LeetCode để cập nhật `submissions` + `streak`.

Ví dụ sync tối đa 90 ngày:
- Trong UI slash command: `/sync_history` rồi nhập `days = 90`.
- Nơi Discord cho nhập inline: `/sync_history days:90`.

## 4) Dữ liệu
Bot ghi dữ liệu vào `data.json` trong thư mục dự án (được mount qua `-v "$(pwd):/app"` nên dữ liệu giữ lại sau khi container restart).

## 5) Lưu ý
- Endpoint LeetCode dùng danh sách AC gần đây, nên sync không đảm bảo all-time 100% nếu lịch sử quá dài/AC quá nhiều.
- Không commit token thật vào git.
