---
title: Trang Quản lý Chiến dịch (Campaigns)
route: "/campaigns"
lang: "vi"
---

# Trang Quản lý Chiến dịch (Campaigns)

> **English:** [`campaigns.md`](campaigns.md)

Trang này là bảng điều khiển hiệu quả chiến dịch tiếp thị (`crm_campaign`). Trang liệt kê toàn bộ chiến dịch trong tenant kèm trạng thái vận hành, trạng thái phê duyệt, chi phí, kết quả chuyển đổi cũng như các thẻ KPI và biểu đồ tổng hợp. Mở trang này từ tab **Campaigns** trên thanh điều hướng. Nhấp vào một dòng để mở trang chi tiết của chiến dịch đó.

## Các thao tác trên trang
- **Tạo chiến dịch (Create campaign)**: Mở Trình soạn thảo chiến dịch ở chế độ tạo mới.
- **Làm mới (Refresh)**: Tải lại toàn bộ thẻ KPI, biểu đồ và bảng danh sách.
- **Tìm kiếm (Search)**: Lọc theo tên hoặc mã chiến dịch (`campaign_code`).
- **Bộ lọc (Filter)**:
  - `Status`: `All Status`, `Active`, `Paused`, `Draft`, `Completed`.
  - `Channel`: `All Channels`, `Paid Search`, `Paid Social`, `Push Notification`, `Email`, `Video`.
  - `Platform`: `All Platforms`, `Google`, `Meta`, `TikTok`, `Zalo`, `Adjust`, `YouTube`.
  - `Objective`: `All Objectives`, `App Install`, `Conversions`, `Leads`, `Awareness`, `Retention`.
- **Sắp xếp (Sort)**: Chọn chỉ số sắp xếp (`Spend`, `Impressions`, `Clicks`, `CTR`, `Conversions`, `CPA`, `Revenue`, `ROAS`) và đảo chiều `DESC` / `ASC`.
- **Đặt lại (Reset)**: Xóa toàn bộ bộ lọc và sắp xếp.
- **Đổi khung thời gian biểu đồ chi tiêu**: `7d`, `30d` hoặc `90d`.
- **Đổi chỉ số Top 5 chiến dịch**: `Conversions` hoặc `ROAS`.
- **Xem chi tiết / Chỉnh sửa**: Nhấp vào dòng hoặc nút **Details** để mở trang chi tiết; nhấn **Edit** để mở trình soạn thảo.
- **Tải thêm (Load more)**: Lấy trang dữ liệu tiếp theo.

## Vòng đời chiến dịch (Campaign Lifecycle)

1. Nhấn **Create campaign**, chọn phân khúc khách hàng, thiết kế workflow và lưu lại.
2. Mở trang chi tiết chiến dịch và nhấn **Submit for approval** (`Draft` → `InReview`).
3. Nếu phiên bản workflow đang ở trạng thái `InReview`, quản trị viên tenant duyệt workflow trước. Sau đó quản trị viên tenant nhấn **Approve campaign**.
4. Quản trị viên tenant nhấn **Start campaign** để gửi `campaign_activation_job` sang Dagster, chuyển trạng thái chiến dịch sang `Running` và thực thi hành trình khách hàng.

## Các phần trên trang
- **Thẻ chỉ số KPI**: `Campaigns`, `Spend`, `Impressions`, `Clicks`, `CTR`, `Conversions`, `CVR`, `Revenue` và `ROAS`.
- **Campaign Spend Trend**: Biểu đồ đường chi phí theo ngày (`7d`, `30d`, `90d`).
- **Top 5 Campaigns**: Biểu đồ cột xếp hạng 5 chiến dịch dẫn đầu theo `Conversions` hoặc `ROAS`.
- **Bảng Campaign Performance**: Các cột `Campaign`, `Status`, `Channel`, `Spend`, `Impr.`, `CTR`, `Conv.`, `CPA`, `ROAS` cùng hai nút **Edit** và **Details**.

## Câu hỏi thường gặp
- **Sự khác nhau giữa Status và Approval State là gì?** `Status` phản ánh trạng thái vận hành thực tế (`Draft`, `Running`, `Paused`, `Completed`, `Failed`), còn `Approval State` phản ánh quy trình kiểm duyệt (`Draft`, `InReview`, `Approved`, `Rejected`).
- **Ai có quyền nhấn Start campaign?** Chỉ quản trị viên tenant (`admin` hoặc `root` / Super Admin) sau khi cả chiến dịch và workflow đã được `Approved`.
- **Chỉ số ROAS là gì?** Return on Ad Spend (`Doanh thu / Chi phí quảng cáo`); chỉ số này càng cao thì hiệu quả chi tiêu càng tốt.
