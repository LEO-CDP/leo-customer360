---
title: Trang Quản lý Chân dung Khách hàng (Personas)
route: "/personas"
lang: "vi"
---

# Trang Quản lý Chân dung Khách hàng (Personas)

> **English:** [`personas.md`](personas.md)

Trang **Persona Management** liệt kê các mẫu chân dung khách hàng dùng chung (`cdp_persona_archetypes`) và hiển thị số lượng hồ sơ chính (`matched_profile_count`) đang khớp với từng mẫu. Tab Personas hiện được ẩn khỏi thanh điều hướng chính và có thể truy cập trực tiếp qua đường dẫn `#/personas`. Nhấp vào một dòng để mở danh sách các hồ sơ khách hàng khớp với mẫu chân dung đó.

## Các thao tác trên trang
- **Bộ lọc (Filter)**: Lọc theo `Domain` (Tất cả lĩnh vực hoặc từng lĩnh vực cụ thể) và `Status` (`All`, `Active`, `Inactive`).
- **Đọc các thẻ KPI**: `Total Personas`, tỷ lệ `Active / Inactive` và độ tin cậy trung bình (`Avg Confidence`).
- **Đọc biểu đồ**: **Personas by Category** (theo danh mục) và **Personas by Risk Level** (theo mức độ rủi ro).
- **Đọc bảng danh sách**: Các cột `Persona Archetype` (tên và `persona_code`), `Domain`, `Category`, `Total Matched Profiles` (`matched_profile_count`), `Status` và `Updated At`.
- **Xem danh sách hồ sơ khớp**: Nhấp vào một dòng hoặc nhấp vào liên kết **Total Matched Profiles**.
- **Thêm mẫu chân dung mới (Add New Persona — chỉ dành cho Admin)**: Mở hộp thoại tạo mới gồm `Domain`, `Persona Code` (`persona_code`), `Persona Name`, `Category`, `Summary`, `LLM Provider`, `LLM Model` và hộp kiểm `Active`; nhấn **Cancel** hoặc **Create Persona**.

## Các phần trên trang
- **Đầu trang & Bộ lọc**: Tiêu đề trang, nút **Add New Persona** và bộ lọc `Domain`, `Status`.
- **Thẻ KPI**: `Total Personas`, `Active / Inactive` và `Avg Confidence`.
- **Biểu đồ**: `Personas by Category` và `Personas by Risk Level`.
- **Bảng Personas**: Mỗi dòng là một mẫu chân dung dùng chung, có nút **Load more** ở cuối bảng để tải thêm.

## Câu hỏi thường gặp
- **Persona Archetype là gì?** Là một định nghĩa chân dung khách hàng dùng chung (`cdp_persona_archetypes`, duy nhất theo `tenant_id + domain + persona_code`), có điểm trọng tâm thành phần và vector nhúng `Vector(768)` để `PersonaResolutionEngine` phân loại các hồ sơ khách hàng vào.
- **Làm sao để xem những khách hàng nào thuộc một chân dung?** Nhấp vào dòng chân dung đó hoặc liên kết **Total Matched Profiles**.
- **Làm sao để tạo một mẫu chân dung mới?** Nhấn **Add New Persona** (yêu cầu quyền Admin), điền mã, tên, lĩnh vực, danh mục rồi nhấn **Create Persona**.
- **Tại sao Avg Confidence hiển thị 0%?** Chưa có hồ sơ khách hàng nào được phân loại vào các mẫu chân dung này.
- **Trạng thái Active có ý nghĩa gì?** Mẫu chân dung (`is_active = TRUE`) đang hoạt động và sẵn sàng để gán cho các hồ sơ khách hàng.
