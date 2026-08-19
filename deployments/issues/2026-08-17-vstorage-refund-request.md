# Support / refund request draft — vStorage failed-create charges

Fill the `<...>` placeholders (account email, customer/tenant ID) before sending.
Send to GreenNode/VNG Cloud support: `support@vngcloud.vn` / helpdesk portal
(`helpdesk.greennode.ai`).

---

## English version

**Subject:** Refund request — 6 vStorage Object Storage "Gold" charges billed for projects that were never created (17/08/2026)

Hello GreenNode / VNG Cloud Support,

On **17/08/2026** I was charged **6 times** for vStorage Object Storage ("vStorage-Gold") when creating a project through the vStorage REST API (`POST /api/v1/projects`). **Every call failed** — the API returned HTTP 200 with `success: false`, `code 114`, `errorMsg: "Could not send order request: Error occurred when creating project"` — **yet my wallet/credit was still charged each time.** No project was actually provisioned.

**Charges to refund (total 3,162,000 VND):**

| # | Time (17/08/2026) | Item | Time period | Amount (VND) |
|---|---|---|---|---|
| 1 | 22:27:14 | vStorage-Gold | 30d | 1,024,000 |
| 2 | 22:33:45 | vStorage-Gold | 30d | 1,024,000 |
| 3 | 22:34:38 | vStorage-Gold | 30d | 1,024,000 |
| 4 | 22:36:52 | vStorage-Gold | 30d | 30,000 |
| 5 | 22:36:54 | vStorage-Gold | 30d | 30,000 |
| 6 | 22:36:56 | vStorage-Gold | 30d | 30,000 |
| | | | **Total** | **3,162,000** |

**Evidence that no resource exists:** `GET /api/v1/projects` returns an empty list (`"datas": null`) on all region endpoints — `hcm03-api`, `hcm04-api`, and `han02-api`.vstorage.vngcloud.vn. So the orders were billed but no project/quota was delivered.

**Likely cause (for your investigation):** the requests were sent to the `hcm03-api` host, and `GET /api/v1/regions` shows object storage is only available in **HCM04** and **HAN02** (not HCM03). It appears the billing order is placed before the region is validated, so an invalid-region create fails (`code 114`) but still charges. You may want to fix this so a failed `POST /projects` does not charge.

**Request:** please **refund all 6 charges (3,162,000 VND)** to my wallet/credit balance, since no vStorage project was provisioned.

Account details:
- Account email: `<your account email>`
- Customer / tenant ID: `<your customer ID>`
- Region attempted: HCM03 (object storage actually in HCM04/HAN02)

Thank you,
`<your name>`

---

## Phiên bản Tiếng Việt

**Tiêu đề:** Yêu cầu hoàn tiền — 6 giao dịch vStorage Object Storage "Gold" bị tính phí nhưng project không được tạo (17/08/2026)

Kính gửi bộ phận Hỗ trợ GreenNode / VNG Cloud,

Vào ngày **17/08/2026**, tôi bị tính phí **6 lần** cho dịch vụ vStorage Object Storage ("vStorage-Gold") khi tạo project qua vStorage REST API (`POST /api/v1/projects`). **Tất cả các lần gọi đều thất bại** — API trả về HTTP 200 với `success: false`, `code 114`, `errorMsg: "Could not send order request: Error occurred when creating project"` — **nhưng ví/số dư của tôi vẫn bị trừ tiền mỗi lần.** Thực tế không có project nào được tạo.

**Các giao dịch cần hoàn (tổng 3.162.000 VND):**

| # | Thời gian (17/08/2026) | Hạng mục | Thời hạn | Số tiền (VND) |
|---|---|---|---|---|
| 1 | 22:27:14 | vStorage-Gold | 30d | 1.024.000 |
| 2 | 22:33:45 | vStorage-Gold | 30d | 1.024.000 |
| 3 | 22:34:38 | vStorage-Gold | 30d | 1.024.000 |
| 4 | 22:36:52 | vStorage-Gold | 30d | 30.000 |
| 5 | 22:36:54 | vStorage-Gold | 30d | 30.000 |
| 6 | 22:36:56 | vStorage-Gold | 30d | 30.000 |
| | | | **Tổng** | **3.162.000** |

**Bằng chứng không có tài nguyên nào tồn tại:** `GET /api/v1/projects` trả về danh sách rỗng (`"datas": null`) trên tất cả các region — `hcm03-api`, `hcm04-api`, và `han02-api`.vstorage.vngcloud.vn. Nghĩa là đơn hàng bị tính phí nhưng không có project/quota nào được cấp.

**Nguyên nhân có thể (để bộ phận kỹ thuật kiểm tra):** các request được gửi tới host `hcm03-api`, trong khi `GET /api/v1/regions` cho thấy Object Storage chỉ có ở **HCM04** và **HAN02** (không có HCM03). Có vẻ như đơn hàng (order) được tạo/tính phí trước khi hệ thống kiểm tra region, nên khi tạo ở region không hợp lệ thì thất bại (`code 114`) nhưng vẫn bị trừ tiền. Mong đội ngũ khắc phục để một `POST /projects` thất bại sẽ không bị tính phí.

**Yêu cầu:** vui lòng **hoàn lại toàn bộ 6 giao dịch (3.162.000 VND)** vào ví/số dư của tôi, vì không có project vStorage nào được cấp.

Thông tin tài khoản:
- Email tài khoản: `<email tài khoản của bạn>`
- Mã khách hàng / tenant: `<mã khách hàng của bạn>`
- Region đã thử: HCM03 (Object Storage thực tế ở HCM04/HAN02)

Trân trọng cảm ơn,
`<tên của bạn>`
