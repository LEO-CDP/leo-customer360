---
title: Nhóm khách hàng
audience: "Marketing | Vận hành"
source: "docs/pages/segments-vi.md, docs/pages/segment-detail-vi.md"
---

Trang **Segments** (nhóm khách hàng) là nơi tạo và quản lý các nhóm khách hàng. Mỗi nhóm có quy tắc riêng, và LEO CDP tính số khách đang khớp.

## Dùng khi nào

- Tạo tệp khách cho chiến dịch.
- Xem ai đang thuộc một nhóm.
- Gắn chuỗi AI chạy tự động trên một nhóm khách.

## Thao tác chính

### Tạo nhóm khách

1. Vào Segments, chọn **Create segment** (tạo nhóm khách).
2. Nhập tên, mã nhóm và ngành.
3. Xây quy tắc bằng một trong hai cách:
   - Chọn thuộc tính và điều kiện trong phần quy tắc.
   - Mô tả bằng lời trong **Describe with AI** (mô tả bằng AI), chọn **Generate rules** (tạo quy tắc), rồi xem lại.
4. Chọn **Create segment**.

### Xem chi tiết nhóm

| Tab | Nội dung |
|---|---|
| Audience SQL Query (truy vấn tệp khách) | Quy tắc của nhóm dạng truy vấn, dành cho kỹ thuật |
| Matched Profiles (hồ sơ khớp) | Danh sách khách đang khớp |
| Agent Workflow (quy trình AI) | Chuỗi AI chạy trên nhóm này |

### Cập nhật số khách

Chọn **Refresh data** (làm mới dữ liệu) trên trang chi tiết, hoặc **Refresh** (làm mới) trên trang danh sách.

## Câu hỏi thường gặp

**Sao nhóm hiện 0 khách?** Chưa có ai khớp điều kiện, hoặc hệ thống chưa tính xong. Kiểm tra quy tắc và chọn **Refresh data**.

**Created by AI agent (tạo bởi AI) nghĩa là gì?** Quy tắc của nhóm do AI soạn nháp. AI không tự lưu, người dùng phải xem lại trước.

**Có viết quy tắc bằng lời được không?** Có, dùng **Describe with AI**.

## Liên quan

- [Chiến dịch](campaigns)
- [Thuộc tính](attributes)
