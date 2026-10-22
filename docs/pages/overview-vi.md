---
title: Bảng điều khiển Tổng quan (Overview Dashboard)
route: "/overview"
lang: "vi"
---

# Bảng điều khiển Tổng quan (Overview Dashboard)

> **English:** [`overview.md`](overview.md)

Trang **Overview** là tab đầu tiên trên thanh điều hướng trên cùng. Trang cung cấp bức tranh toàn cảnh của tenant về quá trình thu thập và phân giải danh tính khách hàng (`/api/v1/reporting/*`): số lượng hồ sơ thô đã tiếp nhận vào `cdp_raw_profiles_stage`, số lượng hồ sơ chính hợp nhất trong `cdp_master_profiles`, tiến độ xử lý của hàng đợi CIR và độ phủ của từng kênh định danh. Đây là bảng điều khiển chỉ đọc (read-only).

## Các thao tác trên trang
- **Đổi khoảng thời gian (Period)**: Chọn tại menu **Period** ở góc trên bên phải. Các tùy chọn: `Last 3 days`, `Last 7 days`, `Last 30 days`, `Last 90 days` (mặc định), `Last 180 days`, `Last 365 days`. Khi đổi khoảng thời gian, toàn bộ thẻ KPI và biểu đồ trên trang sẽ được tải lại.

## Các phần trên trang
**Thẻ chỉ số KPI (KPI cards)** — tính trong khoảng thời gian đã chọn:
- **Raw Profiles**: Tổng số bản ghi hồ sơ thô (`cdp_raw_profiles_stage`) nhận từ các hệ thống nguồn.
- **Master Profiles**: Số hồ sơ khách hàng hợp nhất (`cdp_master_profiles`) được tạo trong kỳ.
- **Duplicate Masters**: Số hồ sơ chính được gộp từ 2 bản ghi hồ sơ thô trở lên (`linked_raw_profile_count >= 2`).
- **Processed**: Số hồ sơ thô đã hoàn tất phân giải danh tính (`status_code = 3`).
- **In Progress**: Số hồ sơ thô đang được xử lý (`status_code = 2`).
- **Pending**: Số hồ sơ thô mới tiếp nhận đang chờ đợt chạy CIR tiếp theo (`status_code = 1`).

**Biểu đồ (Charts)**
- **Profiles by Status**: Biểu đồ tròn thể hiện tỷ lệ hồ sơ thô theo trạng thái (`processed`, `in progress`, `new`, `inactive`, `deleted`).
- **Profiles by Domain**: Biểu đồ cột so sánh số lượng hồ sơ thô và hồ sơ chính theo từng lĩnh vực kinh doanh (`retail`, `banking`, `real_estate`, `travel`, `media`, `education`).
- **Raw Profiles by Source System**: 10 hệ thống nguồn (`source_system`) gửi nhiều hồ sơ thô nhất kèm nhãn lĩnh vực.
- **Identity Graph Coverage**: Tỷ lệ phần trăm hồ sơ chính có chứa từng loại định danh (`email`, `phone`, `device ID`, `advertising ID`, `cookie ID`, `external ID`, `national ID`).

## Câu hỏi thường gặp
- **Hiện có bao nhiêu khách hàng hợp nhất?** Xem thẻ **Master Profiles** trong khoảng thời gian đã chọn.
- **Dữ liệu có đang chờ xử lý không?** Xem hai thẻ **In Progress** và **Pending**, hoặc biểu đồ **Profiles by Status**.
- **Dữ liệu khách hàng đến từ những nguồn nào?** Xem biểu đồ **Raw Profiles by Source System**.
- **Độ phủ định danh của khách hàng đạt bao nhiêu %?** Xem biểu đồ **Identity Graph Coverage**.
- **Tại sao số liệu thay đổi khi tôi đổi Period?** Mọi chỉ số trên trang đều lọc theo khoảng thời gian đã chọn; hãy mở rộng lên `180` hoặc `365 days` để xem toàn bộ lịch sử.
