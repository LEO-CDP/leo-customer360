---
title: Bảng điều khiển Phân tích (Analytics Dashboard)
route: "/analytics"
lang: "vi"
---

# Bảng điều khiển Phân tích (Analytics Dashboard)

> **Bản tiếng Anh (English version):** [`analytics.md`](analytics.md)

Trang **Analytics** là tab thứ hai trên thanh điều hướng trên cùng. Trang này hiển thị lưu lượng sự kiện hành vi từ kho dữ liệu S3/MinIO (`GET /api/v1/events/` và `GET /api/v1/events/device-types`) cùng sự phân bổ hồ sơ khách hàng theo hệ thống nguồn (`source_system`) và lĩnh vực kinh doanh (`domain`). Đây là bảng điều khiển chỉ đọc (read-only).

## Các thao tác trên trang
- **Đổi khoảng thời gian (Period)**: Chọn tại menu **Period** ở góc trên. Các tùy chọn: `Last 3 days`, `Last 7 days`, `Last 30 days` (mặc định), `Last 90 days`, `Last 120 days`, `Last 180 days`.
- **Làm mới dữ liệu (Refresh)**: Nhấn nút **Refresh** để tải lại toàn bộ thẻ KPI và biểu đồ theo khoảng thời gian đang chọn.

## Các phần trên trang
**Thẻ chỉ số KPI (KPI cards)**
- **Total Events**: Tổng số sự kiện hành vi ghi nhận trong khoảng thời gian đã chọn.
- **Active Profiles**: Mặc dù có nhãn là Active Profiles, thẻ này hiện hiển thị tổng số hồ sơ chính (`Master Profiles`) trong khoảng thời gian đã chọn.
- **Conversions**: Luôn hiển thị `"—"`; bộ tổng hợp chỉ số chuyển đổi riêng chưa được nối vào thẻ này.
- **Master Profiles**: Tổng số hồ sơ khách hàng hợp nhất (`cdp_master_profiles`) trong khoảng thời gian đã chọn.

**Biểu đồ (Charts)**
- **Event Volume Over Time**: Biểu đồ đường thể hiện tổng số sự kiện theo từng ngày UTC trong kỳ.
- **Events by Device Type**: Biểu đồ cột thống kê số lượng sự kiện theo loại thiết bị (`desktop`, `mobile`, `tablet`, `unknown`) được phân tích từ `User-Agent`.
- **Profile Distribution Heatmap (Source System × Domain)**: Biểu đồ nhiệt dạng lưới thể hiện số lượng hồ sơ thô (`cdp_raw_profiles_stage`), mỗi hàng là một hệ thống nguồn (`source_system`) và mỗi cột là một lĩnh vực (`domain`); ô càng đậm thì số lượng hồ sơ càng nhiều.
- **Event Activity Heatmap (Daily Event Volume)**: Biểu đồ nhiệt dạng lịch theo ngày kèm dòng tóm tắt (`"N events across M active days (last D days) · peak P/day"`) và thang màu từ ít đến nhiều (`Less–More`).

Nếu không có sự kiện và không có dữ liệu phân bổ hồ sơ trong kỳ, trang sẽ hiển thị thông báo `"No analytics data available"`.

## Câu hỏi thường gặp
- **Có bao nhiêu sự kiện đã diễn ra trong kỳ?** Xem thẻ **Total Events** hoặc biểu đồ đường **Event Volume Over Time**.
- **Khách hàng sử dụng thiết bị nào nhiều nhất?** Xem biểu đồ cột **Events by Device Type**.
- **Hệ thống nguồn nào gửi nhiều hồ sơ nhất?** Xem biểu đồ nhiệt **Profile Distribution Heatmap (Source System × Domain)**.
- **Tại sao thẻ Conversions lại trống ("—")?** Dữ liệu tổng hợp chuyển đổi hiện chưa được kết nối vào thẻ này nên luôn hiển thị `"—"`.
- **Tại sao số Active Profiles lại bằng Master Profiles?** Thẻ mang nhãn **Active Profiles** nhưng hiện đang hiển thị cùng số tổng hồ sơ chính (`Master Profiles`) trong kỳ.
