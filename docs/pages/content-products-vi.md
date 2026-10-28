---
title: Trang Quản lý Nội dung và Sản phẩm (Content & Products)
route: "/content-products"
lang: "vi"
---

# Trang Quản lý Nội dung và Sản phẩm (Content & Products)

> **English:** [`content-products.md`](content-products.md)

Trang này là danh mục nội dung (`cdp_content_items`) và sản phẩm (`cdp_product_items`) thuộc sở hữu của tenant, được dùng cho đề xuất cá nhân hóa, chương trình khuyến mãi và các tác nhân AI xếp hạng (ranking agents). Mở từ tab **Content & Products** trên thanh điều hướng. Trang gồm hai không gian làm việc: **Contents** (bài viết, tin tức, video) và **Products** (bản ghi sản phẩm gốc liên kết với nội dung hiển thị cho khách hàng).

## Các thao tác trên trang
- **Chuyển không gian làm việc**: Chuyển đổi giữa hai tab **Contents** và **Products** ở phía bên trái.
- **Thêm mục mới (Add item)**: Mở biểu mẫu tạo mới, chọn loại **Content** hoặc **Product** rồi điền thông tin.
- **Các trường của Content**: `Domain`, `Content type` (`Article`, `News`, `Video`), `Title` (bắt buộc), `Summary`, `Image URL`, `CTA URL`, `CTA label` và `Keywords / segment tags` (cách nhau bởi dấu phẩy).
- **Các trường của Product**: Gồm các trường hiển thị giống Content, cộng thêm **Product source details**: `Source ID`, `Source type`, `Product ID type` (`SKU`, `ISBN-13`, `TICKER`), `Product ID` (bắt buộc), `Original price` (giá gốc), `Sale price` (giá bán), `Currency` (tiền tệ) và `Extended attributes` (JSON).
- **Nhập hàng loạt từ file TSV (Import TSV)**: Kiểm tra hợp lệ từng dòng trước khi đưa file lên S3 và kích hoạt job Dagster nhập dữ liệu (`content_import.py` / `product_import.py`). File phải có định dạng `.tsv`, dung lượng tối đa **10 MiB** và tối đa **5.000 dòng**. Quá trình nhập chạy bất đồng bộ và trả về mã `Dagster run ID`.
- **Tìm kiếm và lọc**: Tìm theo từ khóa, lọc theo `Domain` và `Content type` (đối với Contents) hoặc theo `Domain` (đối với Products). Nhấn **Clear filters** để đặt lại.
- **Xem chi tiết / chỉnh sửa**: Nhấp vào một mục trong danh sách để xem chi tiết hoặc cập nhật.

## Câu hỏi thường gặp
- **Làm sao để thêm một bài viết hoặc nội dung mới?** Nhấn **Add item**, chọn **Content**, chọn lĩnh vực (`Domain`), loại nội dung (`Content type`) và nhập tiêu đề (`Title`), sau đó nhấn **Save item**.
- **Làm sao để nhập hàng loạt sản phẩm hoặc nội dung?** Nhấn **Import TSV**, chọn file `.tsv` (dưới 10 MiB, tối đa 5.000 dòng) và nhấn **Validate & import**; hệ thống sẽ kiểm tra từng dòng trước khi đưa vào hàng đợi xử lý.
- **Mục Products lưu dữ liệu gì?** Lưu danh mục sản phẩm gốc (`cdp_product_items`) định danh theo nguồn và mã `SKU`, `ISBN-13` hoặc `TICKER`, liên kết với `cdp_content_items` để phục vụ cá nhân hóa và chiến dịch tiếp thị.
- **Làm sao để tìm nhanh một mục?** Sử dụng ô tìm kiếm trên đầu bảng và lọc theo `Domain` hoặc `Content type`.
