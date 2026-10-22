---
title: Trang Soạn thảo Chiến dịch (Campaign Editor)
route: "/campaigns/:id/edit"
lang: "vi"
---

# Trang Soạn thảo Chiến dịch (Campaign Editor)

> **English:** [`campaign-editor.md`](campaign-editor.md)

Trang này dùng để tạo mới hoặc chỉnh sửa một chiến dịch tiếp thị (`crm_campaign` cùng phiên bản hành trình khách hàng `crm_flow_version`). Mở bằng nút **Create campaign** trên trang Campaigns hoặc nút **Edit** trên từng dòng chiến dịch.

## Các thao tác trên trang
- **Tạo chiến dịch mới**: Nhấn **Create campaign**, điền các trường bắt buộc và nhấn **Save campaign**. Bắt buộc phải có tên chiến dịch, một phân khúc khách hàng đang hoạt động đã tính toán (`is_active = TRUE`) và một luồng công việc (workflow) có nút kích hoạt sự kiện (Event trigger).
- **Chỉnh sửa chiến dịch**: Mở chiến dịch hiện có, cập nhật thông tin, phân khúc hoặc sơ đồ hành trình khách hàng rồi nhấn **Save campaign**.
- **Quay lại hoặc hủy**: Nhấn **Back to campaigns** hoặc **Cancel**.
- **Chọn phân khúc mục tiêu (Audience segment)**: Nhấn **Find segment** để mở hộp thoại **Choose audience segment**; tìm theo tên hoặc tag rồi chọn. Nút **Change** để đổi phân khúc khác, nút xóa để gỡ bỏ.
- **Thiết lập thông tin chung (Campaign metadata)**: `Name`, `Campaign code`, `Operational status`, `Channel`, `Platform`, `Language`, `Description`, `Keywords`, `Start date`, `End date`, `Budget amount` và `Currency`.
- **Thiết lập lập kế hoạch (Campaign planning)**: `Audience segment`, `Objective`, `Strategy summary` và `Planning constraints`.
- **Thiết kế hành trình khách hàng (Workflow studio)**: Sử dụng trình kéo thả trực quan (`workflow-core.js`) để cấu hình nút Event trigger, nhánh điều kiện, nút chờ và các nút hành động (`email`, `notification`, `push`, `update_profile`, `run_data_synch_job`).

## Các phần trên trang
- **Campaign metadata**: Tên, mã, trạng thái vận hành, kênh, nền tảng, ngôn ngữ, mô tả, từ khóa, lịch chạy và ngân sách.
- **Campaign planning**: Phân khúc mục tiêu, mục tiêu chiến dịch, tóm tắt chiến lược và ràng buộc lập kế hoạch (phần kế hoạch AI `ai_plan` chỉ đọc).
- **Customer journey workflow**: Trình thiết kế luồng hành trình khách hàng, tải sẵn hồ sơ mẫu từ phân khúc đã chọn để mô phỏng ngay trên trình duyệt.
- **Thanh chân trang (Footer)**: **Cancel** và **Save campaign**. Lưu ý: nhấn lưu chỉ ghi lại bản nháp chứ chưa phê duyệt hay kích hoạt chiến dịch.

## Quy trình sau khi lưu

1. Chuyển sang trang chi tiết chiến dịch và nhấn **Submit for approval** để đưa chiến dịch từ `Draft` sang `InReview`.
2. Nếu phiên bản workflow đang ghim ở trạng thái `InReview` (do AI tạo), quản trị viên tenant (`admin` hoặc `root`) duyệt workflow trước. Workflow do người dùng trực tiếp lưu trên trình soạn thảo thường đã ở trạng thái `Approved`.
3. Quản trị viên tenant nhấn **Approve campaign**.
4. Quản trị viên tenant nhấn **Start campaign** để gửi `campaign_activation_job` sang Dagster.

## Câu hỏi thường gặp
- **Tại sao tôi không nhấn Save campaign được?** Phải nhập tên chiến dịch, chọn phân khúc đang hoạt động, có nút Event trigger trong workflow và ngày kết thúc không được nhỏ hơn ngày bắt đầu.
- **Có thể khởi chạy chiến dịch ngay từ trang soạn thảo không?** Không. Các bước gửi duyệt, phê duyệt và nhấn **Start campaign** thực hiện tại trang Chi tiết chiến dịch.
- **Điều gì xảy ra khi chỉnh sửa một chiến dịch đã được Approved?** Mọi thay đổi nội dung sẽ đưa chiến dịch về lại luồng chờ duyệt (`InReview`) để đảm bảo kiểm soát an toàn trước khi chạy.
- **Ai có quyền phê duyệt và khởi chạy chiến dịch?** Quản trị viên của tenant (`admin`) hoặc `root` / Super Admin.
