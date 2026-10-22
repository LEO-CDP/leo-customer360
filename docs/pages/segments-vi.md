---
title: Trang Quản lý Phân khúc Khách hàng (Segments)
route: "/segments"
lang: "vi"
---

# Trang Quản lý Phân khúc Khách hàng (Segments)

> **English:** [`segments.md`](segments.md)

Đây là Trình tạo phân khúc khách hàng (**Audience Builder** — bảng `cdp_segments`): danh sách các phân khúc khách hàng đã lưu của tenant. Mỗi phân khúc là một tập hợp quy tắc lọc (`json_rules` và `sql_rules`) trên các thuộc tính hồ sơ chính (`cdp_master_profiles`) và thuộc tính mở rộng theo lĩnh vực (`cdp_domain_profiles.domain_attributes`), đồng thời hỗ trợ gắn nhãn đối tượng chuẩn IAB Audience Taxonomy 1.1 (`audience_tags`). Bảng danh sách hiển thị số lượng hồ sơ chính đang khớp (`member_count`). Nhấp vào một dòng để mở trang chi tiết phân khúc.

## Các thao tác trên trang
- **Tìm kiếm (Search)**: Ô `"Search segments by name…"` lọc danh sách ngay khi gõ.
- **Bộ lọc (Filter)**:
  - `Domain`: Tất cả lĩnh vực hoặc một lĩnh vực kinh doanh cụ thể.
  - `Status`: `All statuses`, `Active`, `Inactive`.
  - `Source`: `All sources`, `Created by user`, `Created by AI agent`.
  - `Audience size`: `All audience sizes`, `No members` (0 thành viên), `Has members` (có thành viên).
- **Xóa bộ lọc (Clear filters)**: Nút biểu tượng đặt lại giúp xóa từ khóa tìm kiếm và toàn bộ bộ lọc.
- **Làm mới toàn bộ (Refresh)**: Gọi `POST /api/v1/segments/admin/recompute-all` để gửi job Dagster (`segmentation_job`) tính toán lại toàn bộ phân khúc đang hoạt động của tenant và cập nhật số lượng hồ sơ khớp. Khi bật SSO (`SSO_LOGIN=true`), thao tác này yêu cầu quyền quản trị viên tenant (`admin` hoặc `root`).
- **Tạo phân khúc mới (Create segment)**: Mở hộp thoại soạn thảo phân khúc.
- **Đọc bảng danh sách**: Các cột `Segment` (tên và `segment_tag`), `Business Domain`, `Created By` (`human` hoặc `ai_agent`), `Matched Profiles` (`member_count`), `Lifecycle` (`Active` hoặc `Inactive`) và `Created On`.
- **Xem chi tiết hoặc chỉnh sửa**: Nhấp vào một dòng để mở trang chi tiết, hoặc nhấn nút **Edit** ngay trên dòng đó.

### Hộp thoại Soạn thảo phân khúc (Segment Editor)
- **Các trường thông tin**: `Segment name`, `Segment tag` (`segment_tag`), `Domain`, nhãn IAB (`audience_tags`) và `Description`.
- **Tạo quy tắc bằng AI (Describe with AI)**: Nhập mô tả bằng ngôn ngữ tự nhiên rồi nhấn **Generate rules** (hoặc **Update rules**). API (`POST /api/v1/segments/from-description`) sẽ phác thảo cây điều kiện vào QueryBuilder; nhấn **Start over** để làm lại.
- **Cây điều kiện lọc (Audience rules)**: Chọn thuộc tính từ danh mục (`GET /api/v1/segments/segmentable-profile-attributes`), toán tử và giá trị.
- **Lưu**: Nhấn **Create segment** hoặc **Save changes**; hệ thống tự động gửi job Dagster tính toán lại phân khúc, đồng thời sensor `segmentation_poll_sensor` (mặc định 60s/lần) tự động cập nhật khi hồ sơ thay đổi.

## Các phần trên trang
- **Đầu trang & Bộ lọc**: Ô tìm kiếm, 4 bộ lọc, nút xóa bộ lọc, nút **Refresh** và nút **Create segment**.
- **Bảng Segments**: Mỗi dòng là một phân khúc đã lưu.
- **Chân trang (Footer)**: Tổng số phân khúc và nút **Load more**.

## Câu hỏi thường gặp
- **Làm sao để tạo một phân khúc mới?** Nhấn **Create segment**, đặt tên và mã tag, thiết lập điều kiện lọc (thủ công hoặc dùng **Describe with AI**) rồi nhấn **Create segment**.
- **Sự khác nhau giữa "Created by user" và "Created by AI agent" là gì?** Ghi nhận quy tắc lọc được tạo thủ công bởi người dùng hay được sinh tự động bởi trợ lý AI (`processed_by`).
- **Tại sao Matched Profiles hiển thị 0?** Chưa có hồ sơ khách hàng nào thỏa mãn điều kiện lọc, hoặc tiến trình tính toán trên Dagster đang chạy; hãy nhấn **Refresh** để cập nhật.
- **Các thuộc tính tùy biến (custom attributes) làm sao để xuất hiện trong trình tạo phân khúc?** Mọi thuộc tính trong `cdp_profile_attributes` có `is_segmentable = TRUE` và `status = 'ACTIVE'` (bao gồm cả các khóa JSONB mới trong `cdp_domain_profiles.domain_attributes` được trigger DB tự động đăng ký) đều xuất hiện ngay trong danh sách chọn trường của QueryBuilder.
