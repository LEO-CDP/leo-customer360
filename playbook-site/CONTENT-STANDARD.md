
# Tiêu chuẩn playbook theo ngành

> **Trạng thái:** bản nháp, chờ boss chốt.
> **Ví dụ đã điền:** [Từ khách truy cập web đến khách mua lại](../docs/playbooks/use-cases/ecommerce.md)

## Phân biệt hai loại tài liệu

| | Tech document | Playbook |
|---|---|---|
| Người đọc | Kỹ sư | End user dùng các chức năng của platform |
| Nơi đặt | [leo-cdp.github.io/leo-customer360](https://leo-cdp.github.io/leo-customer360), giữ nguyên | Domain riêng, ví dụ `playbook.leocdp.com`, build từ file Markdown |
| Nội dung | Kiến trúc, API, dữ liệu | Use case, tính năng, roadmap |

## Cấu trúc site playbook

| Mục | Nội dung | Nguồn |
|---|---|---|
| Use cases | Playbook theo ngành | [Customer Journey Map](../docs/marketing/customer-journey-map-vi.md) |
| Tính năng | Mỗi chức năng làm được gì, dùng thế nào | `docs/pages/` |
| Roadmap | Đã có gì, sắp có gì | [ROADMAP](../docs/architecture/ROADMAP.md), viết lại cho end user |

## Tiêu chuẩn nội dung

- **Nguồn:** chỉ lấy từ ba nguồn trong bảng trên. Không tự thêm số liệu, tình huống hay lời khuyên ngoài các nguồn này.
- **Người đọc:** end user, không phải kỹ sư.
- **Ngôn ngữ:** dùng từ của ngành. Không đưa tên bảng, API, SQL, JSON hay tên service vào thân bài.
- **Tên nút:** giữ đúng như trên giao diện và in đậm, ví dụ **Create campaign**.
- **Trung thực:** chỉ mô tả việc sản phẩm làm được. Việc phải làm tay thì ghi rõ.

## Khung một playbook

| # | Phần | Lấy từ tài liệu gốc |
|---|---|---|
| 1 | Tiêu đề và thông tin: ngành, mô hình, kênh, người đọc | Bảng use case, cấu hình chiến dịch |
| 2 | Mục tiêu kinh doanh | Mục tiêu kinh doanh |
| 3 | Hành trình khách hàng | Đồ thị workflow |
| 4 | Tám giai đoạn | Các bước của use case, danh mục KPI |
| 5 | Cần chuẩn bị | Tác nhân và thành phần dữ liệu |
| 6 | Thiết lập trên LEO CDP | Cấu hình chiến dịch, `docs/pages/` |
| 7 | AI hỗ trợ gì | Phân công agent |
| 8 | Quy tắc an toàn | Bước 3 và 5 của UC1, mục 8 |
| 9 | Xử lý sự cố | Mục 7, chỉ giữ tình huống người dùng gặp |

## Tiêu chuẩn thiết kế HTML

Bám sát gần như y hệt trang tài liệu Adobe Experience League về bố cục, cỡ chữ và màu. Không dùng logo, tên sản phẩm hay font Adobe Clean của Adobe; thay bằng tên LEO và font Source Sans 3.

| Thành phần | Tiêu chuẩn |
|---|---|
| Khung trang | Header 52px, cột trái 280px, nội dung tối đa 747px, cột phải 280px |
| Cột trái | Ô lọc từ khóa, danh sách playbook, mục đang mở được tô nền |
| Cột phải | "Trên trang này", liệt kê các phần của trang |
| Đầu bài | Breadcrumb, H1, dòng thông tin, đường kẻ, hộp "Trong trang này" nền xám |
| Chữ | Source Sans 3. H1 44px, H2 28px, đều đậm 700, nội dung 16px |
| Màu | Chữ `#4a4a4a`, link `#0268e3`, nền hộp `#f8f8f8` bo góc 7px, có chế độ tối |
| Hành trình | Sơ đồ Mermaid `flowchart TD`, khối điều kiện bo tròn, class `c` Khách hàng, `l` LEO CDP, `a` Điều kiện |
| Bảng | Có viền bo góc, cuộn ngang riêng khi hẹp |
| Điện thoại | Ẩn cột phải, cột trái lên trên, không cuộn ngang trang |

## Điểm cần boss chốt

1. Khung 9 phần đã đủ chưa?
2. Giao diện bám sát Adobe, dùng tên LEO và font Source Sans 3, có đúng ý không?
3. Thông tin kỹ thuật như mã agent có ẩn hoàn toàn khỏi người đọc không?
4. Viết tiếng Việt trước rồi dịch sang tiếng Anh, hay làm song song?
5. `playbook.leocdp.com` mở công khai hay cần đăng nhập?
