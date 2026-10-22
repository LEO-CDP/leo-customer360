---
title: Trang Danh mục Thuộc tính Hồ sơ (Attribute Catalog)
route: "/attributes"
lang: "vi"
---

# Trang Danh mục Thuộc tính Hồ sơ (Attribute Catalog)

> **Bản tiếng Anh (English version):** [`attributes.md`](attributes.md)

Trang này là danh mục siêu dữ liệu của các thuộc tính hồ sơ khách hàng (`cdp_profile_attributes`): định nghĩa các trường dữ liệu mà nền tảng quản lý, bảng nguồn lưu trữ của từng trường, và cách chúng được dùng trong Phân giải danh tính (CIR) cũng như Trình tạo phân khúc (Segment Builder). Mở trang này từ tab **Attributes** trên thanh điều hướng. Bạn có thể thêm thuộc tính mới và chỉnh sửa thuộc tính hiện có (không có thao tác xóa trên giao diện).

## Các thao tác trên trang
- **Tìm kiếm (Search)**: Ô `"Search name, code, description…"` lọc danh sách ngay khi gõ.
- **Lọc theo nhóm (Group)**: `All groups` hoặc các nhóm có trong danh mục (`General`, `Identity`, `Identity Graph`, `Retail`, `Banking`, `Real Estate`, `Travel`, `Media`, `Education`, `Marketing`, `Lineage`, `Lifecycle`).
- **Lọc theo lĩnh vực (Domain)**: `All domains`, `Global` (`all`), hoặc từng lĩnh vực kinh doanh của tenant.
- **Lọc theo bảng nguồn (Source)**: `All sources`, `Master Profile` (`cdp_master_profiles`), `Domain Profile` (`cdp_domain_profiles`), `Raw Profile` (`cdp_raw_profiles_stage`).
- **Thêm thuộc tính (Add Attribute)**: Mở hộp thoại đăng ký thuộc tính mới.
- **Đọc bảng danh mục**: Các cột `Attribute` (tên hiển thị và mã `attribute_internal_code`), `Group`, `Domain`, `Source`, `Data Type`, `PII`, `Segmentable`, `CIR`, `Priority`, `Conversion` và `Status`.
- **Chỉnh sửa thuộc tính (Edit)**: Nhấn nút **Edit** trên dòng tương ứng; dòng không mở sang trang chi tiết riêng.

### Biểu mẫu Thêm / Sửa thuộc tính
- **Các trường thông tin**: `Attribute Code` (`attribute_internal_code`), `Display Name`, `Description`, `Group`, `Source Table`, `Domain Scope`, `Data Type`, `Contains PII` (`is_pii`) và `Segmentable` (`is_segmentable`).
- **Tùy chọn Nhóm (Group)**: `General`, `Identity`, `Identity Graph`, `Retail`, `Banking`, `Real Estate`, `Travel`, `Media`, `Education`, `Marketing`, `Lineage`, `Lifecycle`.
- **Tùy chọn Bảng nguồn (Source Table)**: `Domain Profile` (`cdp_domain_profiles`), `Master Profile` (`cdp_master_profiles`), `Raw Profile` (`cdp_raw_profiles_stage`).
- **Tùy chọn Phạm vi lĩnh vực (Domain Scope)**: `All (Global)`, `Retail`, `Banking`, `Real Estate`, `Travel`, `Media`, `Education`.
- **Tùy chọn Kiểu dữ liệu (Data Type)**: `Text`, `Numeric`, `Boolean`, `Date`, `Timestamp`, `Array`, `JSONB`.
- **Lưu thay đổi**: Nhấn **Save Attribute** khi thêm mới hoặc **Save Changes** khi chỉnh sửa; **Cancel** để đóng. Trường `Attribute Code` không thể thay đổi sau khi tạo.

> **Lưu ý kỹ thuật:** Mọi khóa JSONB mới được ghi vào `cdp_domain_profiles.domain_attributes` cũng sẽ được trigger cơ sở dữ liệu `trg_register_domain_profile_attributes` tự động đăng ký vào bảng `cdp_profile_attributes` với cờ `is_segmentable = TRUE`.

## Câu hỏi thường gặp
- **Cờ PII có ý nghĩa gì?** Đánh dấu thuộc tính (`is_pii = TRUE`) chứa thông tin định danh cá nhân nhạy cảm để người vận hành và quy trình bảo mật kiểm soát.
- **Cờ Segmentable có ý nghĩa gì?** Cho biết thuộc tính (`is_segmentable = TRUE`) có được hiển thị trong bộ chọn điều kiện của Trình tạo phân khúc (`GET /api/v1/segments/segmentable-profile-attributes`) hay không.
- **Cột CIR và Priority là gì?** **CIR** (`is_identity_resolution = TRUE`) đánh dấu thuộc tính được dùng làm khóa khớp nối danh tính trong `CustomerIdentityResolver`; **Priority** (`priority_rank`) là thứ tự ưu tiên của khóa định danh đó.
- **Nhãn Conversion có ý nghĩa gì?** Thuộc tính thuộc nhóm chấm điểm tiềm năng/chuyển đổi (lead-scoring).
- **Làm sao để thêm một thuộc tính mới?** Nhấn **Add Attribute**, nhập mã `Attribute Code` và tên hiển thị, chọn nhóm, bảng nguồn, lĩnh vực và kiểu dữ liệu, sau đó nhấn **Save Attribute**.
