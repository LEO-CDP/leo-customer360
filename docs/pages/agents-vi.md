---
title: Trang Quản lý Tác nhân AI (AI Agents)
route: "/agent"
lang: "vi"
---

# Trang Quản lý Tác nhân AI (AI Agents)

> **English:** [`agents.md`](agents.md)

Trang này là danh mục đăng ký các tác nhân AI (AI Agents), mô hình học máy và bộ quy tắc (`cdp_ai_agents`) dùng trong Customer 360 cho chấm điểm, dự đoán, đề xuất nội dung/sản phẩm và sinh bản nháp chiến dịch/phân khúc bằng LLM. Mở từ tab **AI Agents** trên thanh điều hướng. Mỗi dòng là một tác nhân; hộp thoại **Add/Edit AI Agent** dùng để xem và sửa cấu hình, lịch chạy và system prompt được phiên bản hóa.

## Các thao tác trên trang
- **Thẻ tổng quan (Summary cards)**: `Total agents` (Đã đăng ký), `Active` (Đang hoạt động & tỷ lệ %), `Training` (Đang huấn luyện), `Failed` (Cần kiểm tra) và `Updated today` (Cập nhật hôm nay).
- **Chuyển tab nhanh**: `All agents`, `Active`, `Training`, `Failed` và `Inactive` (kèm số đếm).
- **Tìm kiếm (Search)**: Ô `"Search agents"` lọc theo tên hiển thị, `agent_code`, tên model hoặc mô tả.
- **Bộ lọc (Filter)**: `Type` (12 loại mô hình chuẩn), `Lifecycle` (`All status`, `Active`, `Inactive`, `Training`, `Deprecated`, `Failed`), `Runtime model` (`Model configured`, `Model not configured`), `Prompt` (`Prompt-backed`, `No prompt`) và `Run mode` (`Scheduled`, `On demand`).
- **Reset**: Xóa từ khóa tìm kiếm và đặt lại toàn bộ bộ lọc.
- **Add AI Agent**: Mở biểu mẫu đăng ký tác nhân AI mới.
- **Đọc bảng**: Các cột `Agent` (tên và `agent_code`), `Agent Type`, `Model`, `Lifecycle`, `Prompt Version`, `Run Schedule`, `Input Features` và `Last Updated`.
- **Chỉnh sửa (Edit)**: Nhấn **Edit** trên từng dòng (không mở trang chi tiết riêng).

### Biểu mẫu Thêm / Sửa AI Agent
- **Các trường thông tin**: `Agent Code` (`agent_code`), `Display Name`, `Runtime Model` (`model_name`), `Description`, `Model Type` (`model_type`), `Schedule Definition (cron)`, `Agent Status`, `Input Features` (cách nhau bởi dấu phẩy, từ `cdp_ai_feature_catalog`), `Hyperparameters` (JSON object), `Prompt Key`, `Prompt Engine`, `System Instructions` và `Required Variables`.
- **Tùy chọn Model Type**: `Classification`, `Regression`, `Clustering`, `Ranking & Recommendation`, `Forecasting`, `Anomaly Detection`, `Uplift Modeling`, `Semantic Embedding`, `Graph ML`, `Optimization`, `Rules Engine`, `Generative LLM`.
- **Tùy chọn Agent Status**: `Active`, `Inactive`, `Training`, `Deprecated`, `Failed`.
- **Lịch chạy (Schedule)**: Chọn mẫu có sẵn (`At midnight`, `Every morning at 09:00`, `Every weekday at 09:00`) hoặc **Custom cron expression** (5 trường).
- **Phiên bản hóa Prompt**: Khi sửa **System Instructions**, hệ thống tự động tăng `instruction_version` và lưu lịch sử vào mảng JSONB `prompt_versions`.
- **Lưu**: Nhấn **Save Agent** (tạo mới) hoặc **Save Changes** (chỉnh sửa); **Cancel** để đóng. Nút **Delete agent** có sẵn khi chỉnh sửa. `Agent Code` không thể thay đổi sau khi tạo.

## Câu hỏi thường gặp
- **Tại sao một agent có trạng thái Failed?** Trạng thái vòng đời là `Failed`; hãy mở **Edit** để kiểm tra lại model runtime, siêu tham số và cấu hình prompt.
- **Sự khác nhau giữa Active và Training là gì?** `Active` nghĩa là tác nhân đã sẵn sàng thực thi; `Training` cho biết mô hình đang trong quá trình huấn luyện hoặc hiệu chỉnh.
- **Làm sao để thêm một AI Agent?** Nhấn **Add AI Agent**, nhập `Agent Code`, tên hiển thị, loại mô hình và trạng thái, sau đó nhấn **Save Agent**.
- **Cột Prompt Version hiển thị gì?** Hiển thị phiên bản câu lệnh nhắc (`v1`, `v2`,…) khi có prompt, hoặc `"No prompt"` nếu không dùng prompt.
- **Cài đặt lịch chạy ở đâu?** Tại trường **Schedule Definition (cron)** trong biểu mẫu.
