---
title: Nguồn dữ liệu
audience: "Vận hành | Đội kỹ thuật"
source: "docs/pages/datasources-vi.md"
---

Trang **Data Sources** (nguồn dữ liệu) là nơi khai báo các nguồn đưa dữ liệu khách hàng vào LEO CDP.

## Dùng khi nào

- Kết nối website, ứng dụng di động hoặc hệ thống khác.
- Lấy mã nhúng để gắn lên website.
- Kiểm tra một nguồn có đang gửi dữ liệu không.

## Thao tác chính

1. Mở tab **Data Sources**, chọn **Add Data Source** (thêm nguồn dữ liệu).
2. Chọn loại nguồn và điền thông tin.
3. Chọn **Save Data Source** (lưu nguồn dữ liệu).
4. Chọn dòng vừa tạo để lấy mã tích hợp, rồi gửi cho đội kỹ thuật.

| Loại nguồn (tên trên màn hình) | Dùng cho |
|---|---|
| Web JavaScript Code (mã nhúng website) | Theo dõi hành vi trên website |
| Data Connector API (kết nối lấy dữ liệu) | Lấy dữ liệu định kỳ từ hệ thống khác |
| Data Webhook API (nhận dữ liệu tức thời) | Nhận dữ liệu tức thời từ hệ thống khác |
| S3 File Connector (nhập từ file) | Nhập dữ liệu theo file |
| Mobile SDK Code (mã cho ứng dụng di động) | Theo dõi hành vi trên ứng dụng di động |

## Câu hỏi thường gặp

**Lấy mã nhúng website ở đâu?** Tạo nguồn loại Web JavaScript Code, chọn dòng vừa tạo, rồi chọn **Copy Web Tag** (sao chép mã nhúng).

**Sao nguồn ở trạng thái Inactive (đang tắt)?** Nguồn đang được đặt là Inactive. Sửa nguồn và chuyển **Status** (trạng thái) sang Active (đang hoạt động).

## Liên quan

- [Tổng quan](overview)
- [Thuộc tính](attributes)
