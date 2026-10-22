---
title: Trang Chi tiết Hồ sơ Khách hàng 360 (Customer Profile Detail)
route: "/profiles/:id"
lang: "vi"
---

# Trang Chi tiết Hồ sơ Khách hàng 360 (Customer Profile Detail)

> **English:** [`profile-detail.md`](profile-detail.md)

Trang này hiển thị toàn bộ thông tin 360° về một khách hàng hợp nhất (`cdp_master_profiles`): định danh, dòng thời gian sự kiện (timeline), chân dung AI (`cdp_customer_personas`), nhãn phân khúc & IAB tags, hồ sơ thô đã nối (`cdp_profile_links`), thuộc tính theo lĩnh vực (`cdp_domain_profiles`) và điểm chấm giá trị/rủi ro. Mở từ tab **Profiles** bằng cách nhấp vào một dòng khách hàng.

## Các thao tác trên trang
- **Chỉnh sửa hồ sơ (Edit profile)**: Mở **Edit Personal Information** (họ tên, email, SĐT, ngày sinh, giới tính, lĩnh vực, nguồn thu thập, hạng khách hàng, KYC, mối quan hệ chính, trạng thái `Active`/`Inactive`/`Deleted`, công ty, địa chỉ và giai đoạn vòng đời `Prospect`, `Lead`, `Customer`, `VIP`, `Dormant`, `Churn risk`). Khi lưu sẽ kích hoạt job Dagster tính lại chân dung (persona).
- **Thêm thuộc tính lĩnh vực (Add Attribute)**: Trong thẻ **Domain Attributes** (`PUT /api/v1/master-profiles/{id}/domain-attributes`); khóa JSONB mới tự động được đăng ký vào `cdp_profile_attributes` để dùng tạo phân khúc.
- **Đổi khoảng thời gian Timeline**: Chọn `24 hours`, `7 days`, `30 days` hoặc `Custom`, sau đó nhấn **Apply** hoặc **Reset**.
- **Xem liên kết định danh**: Nhấn **View details** trong **Identity Graph Links** để xem bản ghi hồ sơ thô và lý do/điểm khớp (`match_method`, `match_score`).
- **Lọc nội dung đề xuất**: Chọn `All`, `News`, `Videos`, `Products` hoặc `Articles` trong **Personalized Items**.

## Bố cục 3 cột trên trang
**Cột bên trái (Left column)**
- **Identity card**: `master_profile_id`, nguồn thu thập, thời điểm đầu/cuối, lĩnh vực, hạng khách hàng và nút **Edit profile**.
- **Customer Persona**: Chân dung AI hiện tại kèm điểm khớp tổng (thang 100), 6 điểm thành phần (`Behavior`, `Engagement`, `Financial`, `Loyalty`, `Relationship`, `Risk Factor`), độ tin cậy, **Next Best Action** và **Recent Transitions**.
- **Kênh kích hoạt & Chi tiết**: `Identity Details`, `Communication Preferences` (`email_opt_in`, `sms_opt_in`, `push_opt_in`, `zalo_opt_in`), `Working Details`, `Address Details`, `Other Attributes` và `Identity Matching Data & Evidence` (`external_ids`, `device_ids`, `advertising_ids`, `cookie_ids`).

**Cột giữa (Center column)**
- **Profile Overview & Data source analytics**: Hạng khách hàng, trạng thái, vòng đời, KYC và thống kê sự kiện theo từng nguồn.
- **Attributes & Segments**: Nhãn `segmentation_tags`, `audience_tags` (IAB) và chủ đề quan tâm.
- **Engagement Summary & Cross-Channel Activity**: Điểm tương tác, lượt đăng nhập, giao dịch, tổng chi tiêu và hoạt động đa kênh.
- **Domain Attributes, Identity Graph Links & Timeline**: Thuộc tính mở rộng theo lĩnh vực, các hồ sơ thô đã gộp và dòng thời gian sự kiện đọc từ S3 (`GET /api/v1/master-profiles/{id}/timeline`).

**Cột bên phải (Right column)**
- **Scoring & Value**: Lead score, Churn risk, CLV dự đoán, CLV lịch sử, độ hoàn thiện hồ sơ và độ tin cậy định danh.
- **Personalized Items**: Bài viết, video và sản phẩm đề xuất cho khách hàng.

## Câu hỏi thường gặp
- **Sửa giai đoạn vòng đời (Lifecycle) hoặc hạng khách hàng ở đâu?** Nhấn **Edit profile** trên thẻ định danh bên trái.
- **Tại sao hiển thị "No persona computed yet"?** Chân dung được tính bất đồng bộ bởi `PersonaResolutionEngine`; thông báo này nghĩa là job chưa chạy cho hồ sơ này.
- **Xem lịch sử hoạt động và điểm CLV/rủi ro ở đâu?** Xem tại thẻ **Timeline** (cột giữa) và **Scoring & Value** (cột phải).
