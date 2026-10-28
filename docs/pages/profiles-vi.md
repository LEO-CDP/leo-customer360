---
title: Trang Danh sách Hồ sơ Khách hàng Hợp nhất (Master Profiles)
route: "/profiles"
lang: "vi"
---

# Trang Danh sách Hồ sơ Khách hàng Hợp nhất (Master Profiles)

> **English:** [`profiles.md`](profiles.md)

Trang này liệt kê toàn bộ hồ sơ khách hàng chính hợp nhất (`cdp_master_profiles`) trong tenant — các bản ghi chuẩn (Golden Records) được xây dựng bởi bộ Phân giải danh tính (CIR) từ hồ sơ thô (`cdp_raw_profiles_stage`) và hồ sơ theo lĩnh vực (`cdp_domain_profiles`). Mở từ tab **Profiles** trên thanh điều hướng. Nhấp vào bất kỳ dòng nào để mở trang chi tiết khách hàng 360°.

## Các thao tác trên trang
- **Tìm kiếm (Search)**: Ô `"Search name, persona, email, phone..."` lọc hồ sơ ngay khi gõ; nút xóa giúp đặt lại từ khóa.
- **Lọc theo ngày (Date range)**: `From` và `To` lọc theo ngày hoạt động cuối hoặc ngày cập nhật hồ sơ (`from_date`, `to_date`).
- **Lọc theo phạm vi hồ sơ (Profile scope)**: `Domain` (Tất cả lĩnh vực), `Data source` (Nguồn dữ liệu `data_source_id`) và `Linked raw profiles` (Số lượng hồ sơ thô tối thiểu đã gộp `linked_raw_profile_count_min`).
- **Lọc theo tín hiệu định danh (Identity signals)**: `Anonymous ID` (`anonymous_id`, bao gồm cả mã khách truy cập inject như `v_...`) và `Device fingerprint` (`device_fingerprint`).
- **Lọc theo phân khúc khách hàng (Customer segmentation)**:
  - `Lifecycle`: `All lifecycle stages`, `Prospect`, `Lead`, `Customer`, `VIP`, `Dormant`, `Churn risk`.
  - `Value tier`: `All value tiers`, `Low Value`, `Medium Value`, `High Value`.
  - `Churn risk`: `All churn risks`, `Low Risk`, `Medium Risk`, `High Risk`, `Critical Risk`.
- **Đọc bảng danh sách**: Các cột `Profile`, `Domain`, `Tier`, `Lifecycle`, `Churn Risk`, `Linked Profiles`, `Total Events` và `Last Activity / Update`.
- **Mở chi tiết khách hàng**: Nhấp vào một dòng bất kỳ để chuyển tới `/profiles/:id`.
- **Phân trang**: Sử dụng nút **Previous** và **Next**; chân trang hiển thị `"Showing X-Y of N profiles"`.

## Các phần trên trang
- **Đầu trang & Bộ lọc**: Ô tìm kiếm văn bản cùng 4 nhóm bộ lọc (`Date range`, `Profile scope`, `Identity signals`, `Customer segmentation`).
- **Bảng Profiles**: Danh sách các hồ sơ chính thỏa mãn điều kiện lọc.
- **Chân trang (Footer)**: Tổng số kết quả và điều khiển phân trang. Nếu không có kết quả khớp, bảng hiển thị `"No profiles matched your filters"` kèm liên kết **Clear search**.

## Câu hỏi thường gặp
- **Làm sao để tìm nhanh một khách hàng?** Nhập tên, nhãn persona, email hoặc số điện thoại vào ô tìm kiếm, hoặc lọc trực tiếp theo **Anonymous ID** / **Device fingerprint**.
- **Cột "Linked Profiles" có ý nghĩa gì?** Là số lượng bản ghi hồ sơ thô đang hoạt động (`cdp_profile_links` có `status = 'ACTIVE'`) đã được CIR gộp vào hồ sơ chính này.
- **Tại sao có hồ sơ hiển thị nhãn Persona thay vì tên thật?** Khi thông tin PII được băm SHA-256 bảo mật (`is_hashed = TRUE`) hoặc chưa có tên thật, hệ thống hiển thị nhãn `persona_name` đại diện.
- **Làm sao để lọc riêng nhóm khách hàng VIP hoặc có nguy cơ rời bỏ cao?** Sử dụng bộ lọc `Lifecycle`, `Value tier` và `Churn risk`.
