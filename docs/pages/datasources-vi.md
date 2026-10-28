---
title: Trang Nguồn Dữ liệu (Data Sources)
route: "/datasources"
lang: "vi"
---

# Trang Nguồn Dữ liệu (Data Sources)

> **English:** [`datasources.md`](datasources.md)

Trang này là danh mục các nguồn dữ liệu và bộ kết nối đầu vào (`sys_data_source`) cung cấp sự kiện hành vi và hồ sơ khách hàng thô cho nền tảng: mã theo dõi Web JavaScript, bộ kéo API phía máy chủ, HTTP Webhook, đồng bộ file lô trên S3/MinIO và Mobile SDK. Mở từ tab **Data Sources** trên thanh điều hướng. Mỗi dòng là một nguồn dữ liệu; nhấp vào dòng để xem lưu lượng sự kiện, thông tin cấu hình, mã QR và đoạn mã tích hợp mẫu.

## Các thao tác trên trang
- **Tìm kiếm (Search)**: Ô `"Search name, slug, url, host…"` lọc danh sách ngay khi gõ.
- **Lọc theo loại nguồn (Type)**: `All types`, `Web JS (Client-side)`, `Data Connector API (Pull)`, `Data Webhook API (Push)`, `S3 File Connector (Batch)`, `Mobile SDK`.
- **Lọc theo trạng thái (Status)**: `All status`, `Active` (`1`), `Inactive` (`0`).
- **Làm mới (Refresh)**: Tải lại danh sách nguồn dữ liệu và các chỉ số lưu lượng.
- **Thêm nguồn dữ liệu (Add Data Source)**: Mở hộp thoại tạo nguồn dữ liệu mới.
- **Đọc bảng**: Các cột `Data Source` (tên và `slug`), `Type`, `Volume Metrics` (`Total`, `Daily Event`, `AVG event / profile`), `Mode` (`Direct` hoặc `Indirect` / `1P` hoặc `3P`) và `Status`.
- **Xem chi tiết nguồn dữ liệu**: Nhấp vào một dòng bất kỳ để mở hộp thoại chi tiết.

### Biểu mẫu Tạo / Sửa nguồn dữ liệu
- **Các trường thông tin**: `Name`, `Slug`, `Source Type` (`1`–`5`), `Status`, `Data Source URL`, `Thumbnail URL`, `Collect Directly` (`is_collect_directly`), `First-Party Data` (`is_first_party_data`), `Data Source Hosts` (danh sách tên miền được phép, cách nhau bởi dấu phẩy) và `Access Tokens & Credentials` (đối tượng JSON lưu trong `access_tokens`).
- **5 loại nguồn dữ liệu (`source_type`)**:
  - `1` — **Web JavaScript Code**: Thu thập sự kiện từ trình duyệt qua `leo.proxy.js` & `leo.observer.js`.
  - `2` — **Data Connector API**: Kéo/đồng bộ dữ liệu định kỳ từ API bên ngoài (Server-side Pull).
  - `3` — **Data Webhook API**: Nhận dữ liệu đẩy thời gian thực qua HTTP POST (`POST /api/v1/tracking/logs`).
  - `4` — **S3 File Connector**: Đồng bộ file lô định dạng CSV, JSONL, Parquet trên S3/MinIO.
  - `5` — **Mobile SDK Code**: Thu thập sự kiện từ ứng dụng iOS / Android / Flutter.
- **Lưu**: Nhấn **Save Data Source** (tạo mới) hoặc **Save Changes** (chỉnh sửa); **Cancel** để đóng. Nút **Delete** có sẵn khi chỉnh sửa.

### Hộp thoại Chi tiết nguồn dữ liệu (Detail Modal)
- **Header & Volume**: Tên, slug, loại nguồn, trạng thái, cùng 3 chỉ số `Total Tracked Events`, `Average Daily Events` và `Avg Events / Profile` (được cập nhật bởi job Dagster `analytics`).
- **Configuration**: `Data Source URL`, `Ingestion Hosts`, `Data Source ID` (`data_source_id`), `Tenant ID`, thời gian tạo/cập nhật và `Access Tokens` (JSON).
- **Bảng mã tích hợp (Integration Panel)** tương ứng với từng loại nguồn:
  - **Web JS**: Đoạn mã nhúng JavaScript kèm nút **Copy Web Tag**.
  - **API Pull**: Thông tin endpoint kéo/đồng bộ dữ liệu.
  - **Webhook**: Câu lệnh mẫu HTTP POST kèm nút **Copy cURL**.
  - **S3 File**: Đường dẫn bucket/partition (`s3://data-tracking-<data_source_id>/...`) và định dạng hỗ trợ.
  - **Mobile SDK**: Đoạn mã tích hợp ứng dụng di động kèm nút **Copy Mobile Code**.
- **Mã QR (QR Code Ingestion & Tracking Details)**: Ảnh QR, `Target URL`, `Tracking URL` (kèm tham số UTM) và thời điểm tạo.

## Câu hỏi thường gặp
- **Làm sao để lấy mã nhúng theo dõi website?** Nhấn **Add Data Source**, chọn loại `1 - Web JavaScript Code`, lưu lại, sau đó nhấp vào dòng vừa tạo và nhấn **Copy Web Tag**.
- **Nhãn Mode "Direct / 1P" có ý nghĩa gì?** Phản ánh hai tùy chọn **Collect Directly** (thu thập trực tiếp) và **First-Party Data** (dữ liệu bên thứ nhất).
- **Tại sao một nguồn dữ liệu ở trạng thái Inactive?** Trạng thái của nó đang đặt là `Inactive`; hãy nhấn **Edit Connector** và chuyển `Status` sang `Active`.
