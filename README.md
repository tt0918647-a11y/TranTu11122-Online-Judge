# TranTu Online Judge — MVP source package

## Có gì trong ZIP
- 500 mã bài TT001–TT500 trong `data/problems.json`.
- Giao diện web: tìm kiếm/lọc/phân trang, trang đề, editor C++17, đăng ký/đăng nhập, lịch sử nộp, bảng xếp hạng.
- Backend Flask + SQLite; mật khẩu được băm; tiến độ/lịch sử được lưu ở server.
- **Trang admin tại `/admin`**: thống kê, xem tài khoản, cấp/thu quyền admin, xem lượt nộp gần đây, sửa nội dung đề và bộ test.
- Kết nối Judge0 để biên dịch/chạy code trong sandbox và so sánh output trên các test được khai báo.
- Chấm theo điểm test 20/30/50; lưu điểm cao nhất của mỗi bài.

## Chạy local bằng Python
1. Cài Python 3.11+.
2. Trong thư mục này, chạy `pip install -r requirements.txt`.
3. Đặt biến môi trường `SECRET_KEY`, `ADMIN_USERNAME`, `ADMIN_PASSWORD` (Flask không tự nạp file `.env`). Có thể sao chép `.env.example` rồi export biến bằng công cụ môi trường của bạn. Đặt mật khẩu admin mạnh trước khi chạy.
4. Chạy `python app.py`.
5. Mở http://localhost:8080. Truy cập `http://localhost:8080/admin`, đăng nhập bằng `ADMIN_USERNAME` và `ADMIN_PASSWORD`.

## Chạy Docker
Tạo file `.env` từ `.env.example`, **đổi `SECRET_KEY` và `ADMIN_PASSWORD` thành giá trị mạnh**, sau đó chạy `docker compose up --build`. Mở http://localhost:8080 và trang quản trị tại http://localhost:8080/admin. SQLite được lưu trong volume `trantu_data`.

## Cấu hình Judge0
Mặc định backend gửi bài tới `https://ce.judge0.com`, dùng language ID 54 (C++17 trên cấu hình Judge0 CE phổ biến). Tùy instance, có thể cần API key; đặt `JUDGE0_URL`, `JUDGE0_TOKEN`, `CPP_LANGUAGE_ID`. Đừng đặt token trong frontend. Judge0 hỗ trợ submit source code, stdin, expected output và trả trạng thái biên dịch/chạy; xem https://ce.judge0.com/.

## LƯU Ý QUAN TRỌNG — chưa phải hệ thống thi chính thức
- Đây là MVP để chạy và phát triển tiếp. Dù có 500 mã bài, nội dung được tạo từ 10 nhóm mẫu với biến thể tiêu đề; chưa phải 500 đề độc lập được biên soạn/kiểm định thủ công. Cần biên tập lại trước khi công khai.
- Mỗi bài hiện có 3 test mẫu được dùng chấm thử, không phải bộ test ẩn đầy đủ. Mô tả subtask là khung 20/30/50 và cần bổ sung test phù hợp từng giới hạn. Không dùng để thi chính thức trước khi kiểm thử lời giải và test.
- Kết nối Judge0 công khai có thể giới hạn tốc độ, thay đổi chính sách hoặc yêu cầu token. Với triển khai thực, nên tự host Judge0 hoặc dùng nhà cung cấp tin cậy, thêm rate limit, CSRF protection, logging, backup và HTTPS. Trang admin mới là chức năng nền tảng, chưa thay thế quy trình kiểm duyệt/bảo vệ production đầy đủ.
- Đổi `SECRET_KEY` trước khi deploy. Không dùng debug mode trên production.

## API chính
- `GET /api/problems`, `GET /api/problems/TT001`
- `POST /api/register`, `POST /api/login`, `POST /api/logout`, `GET /api/me`
- `POST /api/submit` (yêu cầu đăng nhập)
- `GET /api/submissions` (yêu cầu đăng nhập), `GET /api/ranking`, `GET /api/health`
