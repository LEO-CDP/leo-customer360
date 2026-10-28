---
title: Trang Quản trị người dùng hệ thống (System Users)
route: "/admin/users"
lang: "vi"
---

# Trang Quản trị người dùng hệ thống (System Users)

> **English:** [`admin-users.md`](admin-users.md)

Trang này liệt kê danh sách tài khoản nhân sự (`sys_user`) được phép đăng nhập vào workspace và các vai trò phân quyền (RBAC roles) đang gắn cho họ. Mở từ tab **Admin** trên thanh điều hướng. Mỗi dòng là một tài khoản; hộp thoại **Add/Edit System User** dùng để cập nhật hồ sơ và gán vai trò.

## Các thao tác trên trang
- **Tìm kiếm (Search)**: Ô `"Search username, email, name…"` lọc danh sách ngay khi gõ (kể cả tên vai trò, chức danh, phòng ban).
- **Lọc trạng thái (Status)**: `All status`, `Active`, `Inactive`.
- **Add System User**: Mở biểu mẫu tạo tài khoản mới kèm bộ chọn vai trò.
- **Đọc bảng**: Các cột `User` (họ tên và username), `Role`, `Email`, `Job Title`, `Department`, `Status` và `Last Login`.
- **Chỉnh sửa (Edit)**: Nhấn **Edit** trên từng dòng (không mở trang chi tiết riêng). Tài khoản `root` có thể quản lý mọi vai trò, kể cả `root`.

### Biểu mẫu Thêm / Sửa người dùng
- **Trường thông tin**: `Username`, `Full Name`, `Email`, `Phone`, `Job Title`, `Department`, `Password`, `Confirm Password`, `Status` và `Assigned roles`.
- **Mật khẩu**: Bắt buộc khi tạo mới (tối thiểu 8 ký tự, nhập 2 lần); ẩn khi chỉnh sửa.
- **Trạng thái**: `Active` (`1`), `Inactive` (`0`).
- **Assigned roles**: Bộ chọn nhiều vai trò (`PUT /api/v1/users/{user_id}/roles`) thay thế các quyền RBAC của người dùng trong tenant. Chỉ `root` mới được phép gán quyền `root`.
- **Lưu**: Nhấn **Save User** (tạo mới) hoặc **Save Changes** (chỉnh sửa); **Cancel** để đóng. Nút **Deactivate** có sẵn khi chỉnh sửa. `Username` không thể đổi sau khi tạo.

## Danh sách 10 vai trò chuẩn

| Vai trò | Mục đích & Phạm vi quyền hạn |
|---|---|
| `root` | Root / Super Admin; toàn quyền quản trị nền tảng và phân quyền RBAC trong tenant |
| `admin` | Quản trị ứng dụng, người dùng, chính sách bảo mật, thanh toán và cài đặt chung |
| `operator` | Vận hành hằng ngày, giám sát, sao lưu và bảo trì dịch vụ |
| `developer` | Quyền kỹ thuật truy cập môi trường phát triển, API và triển khai |
| `auditor` | Quyền chỉ đọc nhật ký kiểm toán (audit logs), lịch sử và bằng chứng bảo mật |
| `partner` | Quyền truy cập giới hạn vào dữ liệu và dashboard đối tác |
| `billing` | Kế toán / Thanh toán; truy cập gói thuê bao, hóa đơn và thanh toán |
| `editor` | Biên tập viên / Quản lý; tạo, sửa, xuất bản và quản lý nội dung nghiệp vụ |
| `contributor` | Cộng tác viên / Tác giả; tạo và chỉnh sửa nội dung do mình sở hữu |
| `viewer` | Quyền chỉ đọc bảng điều khiển (dashboard), báo cáo và dữ liệu |

## Quy tắc phân quyền và phiên đăng nhập

- Quản lý người dùng hệ thống yêu cầu quyền `root` hoặc quản trị viên tenant (`admin`).
- Chỉ tài khoản `root` mới được phép gán quyền `root` cho người khác và không thể tự gỡ quyền `root` cuối cùng của chính mình.
- Thay đổi vai trò có hiệu lực trên token cấp mới; hãy đăng xuất và đăng nhập lại sau khi tự đổi quyền của mình.
- Cô lập dữ liệu theo tenant được thực thi bởi API và PostgreSQL RLS.

## Câu hỏi thường gặp
- **Làm sao để thêm nhân viên mới?** Nhấn **Add System User**, đặt `Username`, `Password` và chọn vai trò, sau đó nhấn **Save User**.
- **Làm sao để cấp hoặc thu hồi quyền?** Mở **Edit**, cập nhật **Assigned roles** hoặc nhấn **Deactivate**.
- **Tại sao không đổi được Username và Password khi sửa?** `Username` cố định sau khi tạo; mật khẩu chỉ đặt lúc tạo mới.
- **Tại sao hiển thị "No role assigned"?** Người dùng chưa được gán vai trò nào đang hoạt động trong tenant hiện tại.
