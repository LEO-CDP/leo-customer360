---
title: Trang Chi tiết Chiến dịch (Campaign Detail)
route: "/campaigns/:id"
lang: "vi"
---

# Trang Chi tiết Chiến dịch (Campaign Detail)

> **English:** [`campaign-detail.md`](campaign-detail.md)

Trang này hiển thị toàn bộ thông tin của một chiến dịch tiếp thị (`crm_campaign`): siêu dữ liệu vận hành, phân khúc mục tiêu, chiến lược AI, luồng hành trình khách hàng đã ghim (`crm_flow_version`), báo cáo hiệu quả, lịch sử phê duyệt và thử nghiệm A/B. Mở bằng cách nhấp vào một dòng trên trang **Campaigns**. Đầu trang hiển thị tên chiến dịch, trạng thái thực thi (runtime status) và trạng thái phê duyệt (approval status).

## Các thao tác trên trang
- **Quay lại / Chỉnh sửa**: Nhấn **Back to campaigns** hoặc **Edit** (mở Trình soạn thảo chiến dịch).
- **Gửi duyệt (Submit for approval)**: Chuyển chiến dịch `Draft` (đã đủ thông tin) sang hàng đợi `InReview`.
- **Duyệt workflow (Approve / Reject workflow)**: Chỉ hiển thị cho quản trị viên tenant (`admin` hoặc `root`) khi phiên bản workflow đang ghim ở trạng thái `InReview`. Workflow do người dùng trực tiếp lưu thường đã `Approved`; còn workflow do AI tạo bắt đầu ở `InReview` và cần một người dùng khác phê duyệt.
- **Duyệt chiến dịch (Approve / Reject campaign)**: Chỉ hiển thị cho quản trị viên tenant khi chiến dịch đang `InReview` và workflow đã `Approved`.
- **Khởi chạy (Start campaign)**: Hiển thị cho quản trị viên tenant sau khi cả workflow và chiến dịch đều `Approved`. Gọi `POST /api/v1/admin/campaigns/{campaign_id}/activate` để gửi `campaign_activation_job` sang Dagster.
- **Chuyển 6 tab**: `Overview`, `Strategy`, `Content plan`, `Performance report`, `History` và `Experiments`.
- **Quản lý thử nghiệm A/B (Experiments)**: Đặt tên, chọn chỉ số chính (`Conversions`, `Revenue`, `ROAS`, `Conversion rate`), thêm ít nhất 2 biến thể kèm phân khúc và % lưu lượng (tổng `100%`), đánh dấu 1 nhóm control rồi nhấn **Create experiment**. Nhấn **Select** ở cột **Winner** để chọn biến thể thắng.

## Các tab trên trang
- **Overview**: Hiển thị **Campaign metadata** (chỉ đọc), thẻ **Target audience** (tên segment, tag, domain, số hồ sơ, `Ready` / `Needs attention`, Segment ID), sơ đồ hành trình khách hàng kèm phiên bản/trạng thái duyệt, và các trường `Campaign code`, `Owner`, `Channel`, `Platform`, `Language`, `Schedule`, `Budget`, `Objective`, `Description`, `Keywords`, `Created`, `Updated`.
- **Strategy**: Tóm tắt chiến lược, kế hoạch AI (`ai_plan`) và nguồn gốc tác nhân AI (chỉ đọc).
- **Content plan**: Bảng kế hoạch nội dung gồm `Position`, `Title`, `Type` và `Role`.
- **Performance report**: Bộ chọn ngày, nút **Refresh** và bảng chi tiết theo ngày (`Date`, `Spend`, `Impressions`, `Clicks`, `Conversions`, `Revenue`). Bốn thẻ KPI phía trên tab luôn hiển thị tổng lũy kế.
- **History**: Lịch sử phê duyệt và nhật ký kiểm toán (`sys_audit_log`) kèm người thực hiện, lý do và thời gian.
- **Experiments**: Danh sách thử nghiệm A/B (`crm_campaign_experiments`) kèm tỷ lệ phân bổ, `Conversions`, `CVR`, `ROAS` và biến thể thắng cuộc.

## Câu hỏi thường gặp
- **Tại sao tôi không thấy nút Approve hoặc Reject?** Nút duyệt chỉ hiện cho quản trị viên tenant khi workflow hoặc chiến dịch đang ở trạng thái `InReview`.
- **Tài khoản `root` có được duyệt và chạy chiến dịch không?** Có. `root` / Super Admin có toàn quyền duyệt workflow, duyệt chiến dịch và nhấn **Start campaign**.
- **Quy trình chuẩn để khởi chạy chiến dịch là gì?** Lưu chiến dịch → **Submit for approval** → duyệt workflow (nếu `InReview`) → **Approve campaign** → **Start campaign**.
- **Điều gì xảy ra sau khi nhấn Start campaign?** Chiến dịch chuyển sang `Running` và gửi `campaign_workflow_job` sang Dagster để thực thi hành trình khách hàng (gửi Email, Zalo ZNS, SMS, Push hoặc đồng bộ tệp quảng cáo Meta/Google/TikTok).
- **Tại sao báo cáo hiển thị số tổng trọn đời?** Khi chưa có dữ liệu chi tiết theo ngày cho khoảng thời gian đã chọn, trang tự động hiển thị tổng lũy kế kèm cảnh báo.
