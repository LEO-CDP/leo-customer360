---
title: Trang Danh sách Hồ sơ Khớp Chân dung (Persona Matched Profiles)
route: "/personas/:archetypeId/matched-profiles"
lang: "vi"
---

# Trang Danh sách Hồ sơ Khớp Chân dung (Persona Matched Profiles)

> **English:** [`persona-matched-profiles.md`](persona-matched-profiles.md)

Trang này hiển thị chi tiết một mẫu chân dung khách hàng dùng chung (`cdp_persona_archetypes`) và danh sách toàn bộ hồ sơ khách hàng chính (`cdp_master_profiles`) đang được gán vào mẫu chân dung đó thông qua bảng `cdp_customer_personas`. Mở trang này bằng cách nhấp vào một dòng chân dung trên trang **Personas**.

## Các thao tác trên trang
- **Quay lại**: Nhấn **Back to personas** để trở về danh sách chân dung.
- **Chỉnh sửa (Edit — chỉ dành cho Admin)**: Mở hộp thoại **Edit Persona Archetype** gồm các trường `Persona Name`, `Category`, `Summary`, `LLM Provider`, `LLM Model` và hộp kiểm `Active`; nhấn **Cancel** hoặc **Save Changes**. Khi lưu, hệ thống sẽ kích hoạt job Dagster bất đồng bộ để cập nhật lại số lượng `matched_profile_count`.
- **Đọc thông tin siêu dữ liệu (Metadata)**: `Domain`, `Category`, `Matched Profiles` (`matched_profile_count`), thời điểm cập nhật (`Updated`), `LLM Provider`, `LLM Model`, nhãn trạng thái `Active`/`Inactive`, mã `persona_code` và đoạn mô tả tóm tắt.
- **Đọc điểm trọng tâm (Centroid Component Scores)**: 6 chỉ số thành phần chuẩn (`Behavior`, `Engagement`, `Financial`, `Loyalty`, `Relationship` và `Risk`) được `PersonaResolutionEngine` dùng làm tâm đối sánh.
- **Mở hồ sơ khách hàng**: Nhấp vào bất kỳ dòng nào trong bảng **Master Profiles** để mở trang chi tiết hồ sơ 360° của khách hàng đó.
- **Phân trang**: Sử dụng hai nút **Prev** và **Next**.

## Các phần trên trang
- **Thẻ Archetype (Archetype card)**: Tên chân dung, trạng thái hoạt động, mã `persona_code`, tóm tắt, các trường siêu dữ liệu và 6 điểm **Centroid Component Scores**.
- **Bảng Master Profiles**: Danh sách tất cả khách hàng đang khớp với mẫu chân dung này (`is_active = TRUE` trong `cdp_customer_personas`), kèm tổng số lượng và nút chuyển trang **Prev/Next**. Nếu chưa có hồ sơ nào khớp, bảng hiển thị `"No master profiles matched to this persona archetype."`

## Câu hỏi thường gặp
- **Điểm Centroid Component Scores là gì?** Là vector điểm chuẩn mục tiêu (gồm Hành vi, Tương tác, Tài chính, Trung thành, Mối quan hệ và Rủi ro) mà `PersonaResolutionEngine` dùng để so khớp với từng hồ sơ khách hàng; các điểm này được hệ thống tính toán tự động.
- **Làm sao để sửa thông tin một mẫu chân dung?** Nhấn nút **Edit** (chỉ dành cho Admin) để cập nhật tên, danh mục, tóm tắt, nhà cung cấp/mô hình LLM hoặc trạng thái `Active`.
- **Tại sao Matched Profiles bằng 0?** Chưa có hồ sơ khách hàng nào khớp với mẫu chân dung này, hoặc tiến trình tính toán persona chưa chạy kể từ khi tạo mới.
- **Làm sao để xem chi tiết một khách hàng trong danh sách?** Nhấp trực tiếp vào dòng của khách hàng đó trong bảng **Master Profiles**.
- **Trạng thái Active có ý nghĩa gì?** Cho biết mẫu chân dung này (`is_active = TRUE`) đang được phép tham gia phân loại và gán cho các hồ sơ khách hàng mới.
