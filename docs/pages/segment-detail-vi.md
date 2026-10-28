---
title: Trang Chi tiết Phân khúc Khách hàng (Segment Detail)
route: "/segments/:id"
lang: "vi"
---

# Trang Chi tiết Phân khúc Khách hàng (Segment Detail)

> **English:** [`segment-detail.md`](segment-detail.md)

Trang này hiển thị chi tiết một phân khúc khách hàng (`cdp_segments`): thẻ thông tin chung, các nhãn đối tượng chuẩn IAB (`audience_tags`) và ba không gian làm việc gồm câu truy vấn SQL sinh tự động, danh sách hồ sơ khách hàng khớp điều kiện, và chuỗi tác nhân AI xử lý cho phân khúc đó (`cdp_segment_agent_workflows`). Mở trang này bằng cách nhấp vào một phân khúc trong danh sách **Segments**.

## Các thao tác trên trang
- **Chỉnh sửa phân khúc (Edit segment)**: Mở hộp thoại soạn thảo để sửa tên, `segment_tag`, lĩnh vực, mô tả, nhãn IAB `audience_tags` và cây điều kiện lọc.
- **Làm mới dữ liệu (Refresh data)**: Gọi `POST /api/v1/segments/{segment_id}/recompute` để gửi job Dagster (`segmentation_job`) tính toán lại `member_count`, `last_computed_at` và đồng bộ nhãn `segmentation_tags` / `audience_tags` lên hồ sơ chính.
- **Chuyển không gian làm việc**: Chuyển đổi giữa 3 tab **Audience SQL Query**, **Matched Profiles** và **Agent Workflow**.

### Tab Audience SQL Query
- Hiển thị câu lệnh SQL hoàn chỉnh (`final_generated_sql` / `sql_rules`) truy vấn trên `cdp_master_profiles` và `cdp_domain_profiles`, kèm nút **Copy SQL**.
- Nếu phân khúc chưa có điều kiện lọc, trang hiển thị `"No SQL rules defined for this segment"` và hướng dẫn nhấn **Edit segment**.

### Tab Matched Profiles
- Liệt kê các hồ sơ khách hàng chính đang khớp với điều kiện của phân khúc (`GET /api/v1/segments/{segment_id}/matched-profiles`); nhấp vào bất kỳ dòng nào để mở trang chi tiết khách hàng 360°.
- Nhấn **Load more** để tải thêm trang tiếp theo.

### Tab Agent Workflow
- Nhấn **Add agent** để thêm một bước xử lý AI; nhấn **Save workflow** để lưu thứ tự thực thi.
- Mỗi bước gồm: chọn **AI agent** (`agent_code`), thứ tự chạy (**Run order** — số nhỏ chạy trước), công tắc trạng thái (**Enabled** hoặc **Paused**), lịch chạy riêng (**Schedule override** dạng cron) và cấu hình tham số (**Agent configuration** dạng JSON).
- Đối với các tác nhân xếp hạng và đề xuất (ranking/recommendation agents), có thể chọn thêm danh sách nội dung/sản phẩm ứng viên (**Candidate content/products**).
- Dùng nút mũi tên lên/xuống để đổi thứ tự và nút **Remove** để xóa một bước.

## Các phần trên trang
- **Thẻ Metadata**: Tên phân khúc, `segment_tag`, nhãn trạng thái `Active`/`Inactive`, nhãn nguồn tạo `Human`/`AI Agent` (`processed_by`), nhãn IAB `audience_tags`, mô tả, `Target Domain`, quy mô tệp (`Audience Size` / `member_count`), thời điểm tính toán gần nhất (`Last Computed`) và ngày tạo (`Created At`).
- **3 Tab chức năng**: `Audience SQL Query`, `Matched Profiles` và `Agent Workflow`.

## Câu hỏi thường gặp
- **Tại sao quy mô tệp (Audience Size) bằng 0?** Chưa có hồ sơ nào khớp điều kiện hoặc phân khúc chưa được tính toán lại; hãy kiểm tra điều kiện và nhấn **Refresh data**.
- **Làm sao để tính toán lại danh sách thành viên?** Nhấn nút **Refresh data** để kích hoạt job Dagster tính toán lại và cập nhật số lượng.
- **Làm sao để thay đổi điều kiện lọc của phân khúc?** Nhấn **Edit segment** để mở trình tạo quy tắc trực quan hoặc dùng AI sinh lại quy tắc.
- **Nhãn Human và AI Agent khác nhau thế nào?** Cho biết điều kiện lọc của phân khúc được tạo thủ công bởi người dùng hay được phác thảo bởi AI (`POST /api/v1/segments/from-description`).
- **Tab Agent Workflow dùng để làm gì?** Dùng để cấu hình chuỗi các tác nhân AI chấm điểm, phân loại và đề xuất cá nhân hóa chạy tự động theo thứ tự trên tệp khách hàng của phân khúc này.
