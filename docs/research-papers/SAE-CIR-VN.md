---
title: "Các nguyên tắc phương pháp luận cho Customer Identity Resolution"
subtitle: "Phương pháp dựa trên bằng chứng và nhận biết nguồn để xây dựng Identity Graph"
author: "Trieu Nguyen"
date: "25 tháng 8, 2026"

geometry:
  - a4paper
  - margin=1.1cm

linestretch: 0.95

mainfont: "Latin Modern Roman"
documentclass: article
papersize: a4
fontsize: 11pt
toc: true
---

\newpage

## Tóm tắt

Customer Identity Resolution (CIR) là một năng lực nền tảng của Customer Data Platform (CDP) hiện đại. Mục đích của CIR là xác định liệu nhiều bản ghi khách hàng thô được tạo ra bởi các hệ thống khác nhau có đại diện cho cùng một cá nhân trong thế giới thực hay không, và khi có đủ bằng chứng thì liên kết các bản ghi đó với một Master Profile thống nhất.

Các phương pháp liên kết bản ghi truyền thống thường chủ yếu tập trung vào việc các thuộc tính nhận dạng như email, số điện thoại hoặc customer ID có khớp hay không. Tuy nhiên, dữ liệu khách hàng được tạo ra trong môi trường omnichannel hiện đại có mức độ tin cậy khác nhau. Một customer identifier đã được xác minh từ CRM nội bộ không nhất thiết phải có cùng trọng số bằng chứng với một số điện thoại do người dùng tự nguyện nhập vào một web survey ẩn danh. Tương tự, device identifier do mobile application tạo ra cung cấp tính liên tục hữu ích về hành vi nhưng không nhất thiết chứng minh được danh tính của một con người.

Tài liệu này đề xuất phương pháp **Source-Aware Evidence-Based Customer Identity Resolution (SAE-CIR)**. Phương pháp đánh giá bằng chứng nhận dạng theo ba chiều chính: **độ mạnh của identity signal, độ tin cậy của source và chất lượng dữ liệu**. Các chiều này tạo ra một evidence score. Khi có dữ liệu đã gắn nhãn mang tính đại diện, score và các đặc trưng so sánh khác có thể được calibration thành match probability để xác định raw profile nên được liên kết với Master Profile hiện có, đưa đi review bổ sung hay dùng để tạo Master Profile mới.

Phương pháp đề xuất còn biểu diễn các quan hệ nhận dạng dưới dạng Identity Graph, cho phép CDP lưu giữ bằng chứng nhận dạng, provenance, score hoặc calibrated probability và thông tin theo thời gian thay vì chỉ đơn giản merge các bản ghi. Nhờ đó, Customer 360, segmentation, personalization, analytics và marketing activation có một nền tảng minh bạch, explainable, auditable và dễ thích ứng hơn.



# 1. Giới thiệu

Khách hàng hiện đại tương tác với tổ chức qua nhiều kênh.

Một cá nhân có thể:

* xem một quảng cáo,
* truy cập website,
* tạo tài khoản,
* sử dụng mobile application,
* gửi feedback form,
* trao đổi qua social media,
* mua hàng qua kênh e-commerce,
* mua hàng tại cửa hàng vật lý,
* tương tác với customer service,
* rồi quay lại bằng một thiết bị khác.

Mỗi tương tác có thể tạo ra một customer record khác nhau.

Ví dụ:

```text
Advertising Platform
    device_id = A123

Website
    anonymous_id = X891
    email = customer@example.com

Mobile Application
    device_id = A123
    phone = 090xxxxxxx

CRM
    customer_id = C001
    email = customer@example.com
    phone = 090xxxxxxx
```

Một data warehouse truyền thống có thể lưu các bản ghi này thành những row riêng biệt.

Tuy nhiên, CDP cần trả lời một câu hỏi nền tảng hơn:

> **Bản ghi nào trong số này thuộc về cùng một người trong thế giới thực?**

Đây chính là bài toán Customer Identity Resolution.

CIR vì vậy không chỉ là một thao tác database deduplication. Đây là một **quy trình suy luận nhận dạng dựa trên bằng chứng**.

Mệnh đề trung tâm của tài liệu này là:

$$
\boxed{
\text{Identity Resolution}
\neq
\text{Simple Record Matching}
}
$$

Thay vào đó:

$$
\boxed{
\text{Identity Resolution}
=
\text{Evidence Evaluation}
+
\text{Confidence Estimation}
+
\text{Identity Graph Construction}
}
$$



# 2. Bài toán nhận dạng khách hàng

**Nhiều biểu diễn của một người**

Một cá nhân trong thế giới thực có thể được biểu diễn bằng nhiều identifier:

$$
P =
{
email,
phone,
device_id,
customer_id,
anonymous_id,
external_id,
\ldots
}
$$

Tuy nhiên, không hệ thống riêng lẻ nào nhất thiết chứa đầy đủ biểu diễn đó.

Ví dụ:

```text
Profile A
Source = Website

device_id = A123
email = customer@example.com
```

```text
Profile B
Source = Mobile App

device_id = A123
phone = 090xxxxxxx
```

```text
Profile C
Source = CRM

customer_id = C001
email = customer@example.com
phone = 090xxxxxxx
```

CDP phải suy luận rằng:

$$
A \approx B \approx C
$$

và xây dựng một biểu diễn nhận dạng thống nhất.

\newpage 

# 3. Mô hình Raw Profile

Gọi một raw profile được định nghĩa như sau:

$$
r_i =
{
id_i,
source_i,
attributes_i,
events_i,
timestamp_i
}
$$

trong đó:

* (id_i) là identifier của bản ghi theo từng source;
* (source_i) xác định hệ thống phát sinh dữ liệu;
* (attributes_i) chứa các thuộc tính nhận dạng và profile;
* (events_i) chứa các behavioral event liên quan;
* (timestamp_i) biểu thị thời điểm quan sát.

Một raw profile có thể có dạng:

```text
Raw Profile
------------------------
source: mobile_app
device_id: A123
email: customer@example.com
phone: 090xxxxxxx
timestamp: 2026-08-24
```

Mục tiêu của CIR là xác định liệu:

$$
r_i \rightarrow M_j
$$

trong đó (M_j) đại diện cho một Master Profile hiện có.



# 4. Master Profile

Master Profile đại diện cho biểu diễn vận hành hiện tại của CDP về một giả
thuyết nhận dạng khách hàng. Nó có thể tương ứng với một khách hàng trong thế
giới thực, nhưng hệ thống không nên xem sự tương ứng đó là chắc chắn chỉ vì một
profile đang tồn tại.

Conceptually:

```text
                    MASTER PROFILE
                         |
          ----------------------------------
          |              |              |
      CRM Profile    App Profile    Web Profile
          |              |              |
          ----------------------------------
                         |
                    Identity Graph
```

Không nên hiểu Master Profile đơn giản là một database row đã được merge.

Biểu diễn phù hợp hơn là:

$$
M_j =
{
identity,
attributes,
links,
evidence,
confidence,
provenance,
events
}
$$

Sự phân biệt này quan trọng vì CDP phải lưu giữ không chỉ identity đã được
resolve mà còn cả **lý do hệ thống tin rằng identity đó là đúng**.

\newpage

# 5. Identity Graph

CIR tạo một Identity Graph biểu diễn các quan hệ giữa raw profile và Master Profile.

Một biểu diễn đơn giản hóa là:

```text
                     Master Profile
                           |
             --------------------------------
             |             |             |
             v             v             v
        Raw Profile    Raw Profile    Raw Profile
          Website          App        Ads Platform
```

Mỗi kết nối là một **Identity Graph Link**.

Một link có thể được biểu diễn như sau:

$$
L_{ij}
=
(r_i,M_j,S,\hat{p},D,E,T)
$$

trong đó:

* (r_i) = raw profile;
* (M_j) = Master Profile;
* (S) = evidence-support score;
* ($\hat{p}$) = calibrated match probability nếu có;
* (D) = quyết định resolution;
* (E) = identity evidence;
* (T) = timestamp.

Phương pháp này có một tính chất quan trọng:

> **Các quyết định nhận dạng trở thành những quan hệ explainable và auditable thay vì các lần merge bản ghi không thể đảo ngược.**


# 6. Identity Signal

CIR đánh giá các identity signal do raw profile cung cấp.

Các signal điển hình gồm:

### 6.1 Signal có thể mạnh

* identifier do tổ chức phát hành, kiểm soát và gắn với authenticated account;
* email hoặc số điện thoại được verify trong authenticated context;
* loyalty hoặc customer identifier có cơ chế kiểm soát issuer và tính duy nhất được ghi nhận;
* account identifier có lần authentication thành công gần đây.

Các signal này chỉ mạnh khi các giả định về issuer, verification, uniqueness và
recency được đáp ứng. Một CRM column hoặc identifier tên là `customer_id` không
tự động là bằng chứng nhận dạng.

### 6.2 Signal phụ thuộc ngữ cảnh

* device ID;
* application user ID;
* cookie hoặc advertising ID;
* external customer ID;
* email hoặc số điện thoại chưa được verify.

Các signal này có thể cung cấp tính liên tục hoặc bằng chứng bổ trợ hữu ích,
nhưng chúng có thể bị chia sẻ, reset, tái sử dụng, sao chép hoặc bị bên khác
kiểm soát.

### 6.3 Signal yếu hoặc mang tính ngữ cảnh

* IP address;
* user-agent;
* geographic location;
* browsing similarity;
* behavioral similarity;
* inferred attributes.

Các nhóm trên là giả thuyết cho một policy ban đầu, không phải chân lý cố định
của ngành. Độ mạnh của signal phụ thuộc vào phương pháp so sánh, acquisition
context, population, collision rate, verification status và recency.

Độ mạnh cụ thể của từng signal nên được cấu hình.

Một signal-weight model mang tính khái niệm là:

| Identity Signal       | Trọng số ví dụ |
| --------------------- | -------------: |
| Verified Customer ID  |           1.00 |
| Verified Email        |           0.95 |
| Verified Phone        |           0.95 |
| Loyalty ID            |           0.90 |
| Authenticated User ID |           0.90 |
| Device ID             |           0.60 |
| Anonymous ID          |           0.40 |
| IP Address            |           0.20 |

Các giá trị này là prior minh họa cho một policy ví dụ, không phải probability
hay hằng số phổ quát. Cần thay thế hoặc điều chỉnh chúng bằng labeled
validation data và theo dõi riêng theo source, signal, population và thời gian.


# 7. Vì sao chỉ độ mạnh của Signal là chưa đủ

Một hạn chế lớn của identity matching truyền thống là giả định:

> Nếu cùng một field khớp, identity evidence có độ tin cậy như nhau.

Giả định này không đúng.

Hãy xét hai số điện thoại.

### Trường hợp A

```text
Source:
Internal CRM

Phone:
0901234567
```

### Trường hợp B

```text
Source:
Anonymous Web Survey

Phone:
0901234567
```

Identity signal là như nhau:

$$
signal = phone
$$

Tuy nhiên, độ tin cậy của source lại khác nhau.

Bản ghi CRM có thể được thu thập trong một authenticated customer transaction,
trong khi câu trả lời survey có thể được nhập thủ công mà không authentication.

Do đó:

$$
\boxed{
\text{Signal Strength}
\neq
\text{Evidence Strength}
}
$$

Evidence strength phải xét đến source.


# 8. Source Trust

Vì vậy, mỗi observation nên có một source-reliability factor có thể cấu hình,
thường chính xác hơn một global score cho toàn bộ hệ thống:

$$
W_{source}(s,k,c) \in [0,1]
$$

Trong đó $s$ là source, $k$ là signal hoặc field, còn $c$ là acquisition
context, chẳng hạn authenticated transaction hoặc anonymous form. Factor này
đại diện cho độ tin cậy kỳ vọng của observation cụ thể đó, không phải
probability rằng phép match người đó là chính xác.

Một model mang tính khái niệm là:

| Source                | Ví dụ                     | Prior minh họa     |
| --------------------- | ------------------------- | ----: |
| Internal CRM          | Verified customer record  |  0.95 |
| Authenticated Account | Login + verified identity |  0.95 |
| POS / Transaction     | Customer / loyalty ID     |  0.90 |
| Mobile App            | Authenticated application |  0.85 |
| Website Login         | Authenticated website     |  0.80 |
| Marketing Platform    | Advertising identifier    |  0.50 |
| External Partner      | Third-party customer data |  0.40 |
| Web Feedback Form     | Self-entered information  |  0.30 |
| Anonymous Survey      | Self-reported feedback    |  0.20 |

Các giá trị này phải được quản trị bằng data-quality policy của tổ chức và
validate với các outcome đã gắn nhãn mang tính đại diện. Không nên sao chép
chúng vào production như những hằng số phổ quát.


# 9. Source Trust và Data Governance

Source trust nên được xem là một **data-governance parameter**, không chỉ là
algorithm parameter.

Ví dụ:

```text
Source Registry
----------------------------------
CRM
  trust = 0.95

Mobile App
  trust = 0.85

Website
  trust = 0.80

Google Ads
  trust = 0.50

External Survey
  trust = 0.20
```

Điều này tạo ra quan hệ trực tiếp giữa:

$$
\text{Data Governance}
\rightarrow
\text{Source Trust}
\rightarrow
\text{CIR}
\rightarrow
\text{Customer 360}
$$

Data-governance team có thể duy trì source policy mà không thay đổi
identity-resolution engine. Tuy nhiên, thay đổi trust policy sẽ thay đổi score
và có thể cả link, nên policy phải được version, phê duyệt, giám sát và
reprocess theo quy trình thay đổi của tổ chức.


# 10. Data Quality

Chỉ source trust cũng chưa đủ.

Một source đáng tin cậy vẫn có thể chứa dữ liệu chất lượng kém.

Ví dụ:

```text
CRM
phone = 090 123 4567
```

thì có thể có chất lượng cao.

But:

```text
CRM
phone = 0901234567 ???
```

có thể chứa lỗi format hoặc validation.

Vì vậy, CIR đưa vào một quality factor ở cấp observation:

$$
W_{quality}(i,k) \in [0,1]
$$

đại diện cho chất lượng của signal $k$ trong observation $i$. Quality nên được
tính từ các check đã được document và không trùng lặp. Ví dụ, normalization,
format validity và verification status không nên được nhân với nhau như thể
chúng độc lập khi một check được suy ra từ check khác.

Ví dụ về quality factor:

* format validity;
* verification status;
* recency;
* completeness;
* consistency;
* duplication;
* expiration;
* normalization quality.

\newpage

# 11. Identity Evidence ba chiều

Do đó, phương pháp đề xuất định nghĩa một evidence contribution minh họa như sau:

$$
\boxed{
E_k =
M_k
\times
W_{signal,k}
\times
W_{source,k}
\times
W_{quality,k}
}
$$

trong đó:

* (M_k) = kết quả so sánh của identity signal (k);
* (W_{signal,k}) = độ mạnh nội tại của identity signal;
* (W_{source,k}) = độ tin cậy của source phát sinh;
* (W_{quality,k}) = chất lượng của dữ liệu được quan sát.

Với một agreement model đơn giản, kết quả so sánh có thể được biểu diễn bởi
$M_k \in [-1,1]$, trong đó giá trị dương biểu thị agreement, giá trị âm biểu thị
contradiction, còn 0 biểu thị dữ liệu thiếu hoặc không có thông tin. $E_k$ thu
được là evidence contribution, không phải probability rằng hai bản ghi thuộc
cùng một người. Các weight phải được định nghĩa cho một signal, source context
và population cụ thể; chúng không phải hằng số phổ quát.

Một ví dụ đơn giản với **CRM, Web Survey và Facebook Ads**:

## 11.1 Ví dụ: Cùng một khách hàng xuất hiện ở 3 source

Giả sử hệ thống đang kiểm tra liệu ba bản ghi có thuộc về **Nguyen Van A** hay không.

| Source       | Signal                | Comparison (M_k) | Signal Weight | Source Trust | Quality | Evidence (E_k) |
| ------------ | --------------------- | ---------------: | ------------: | -----------: | ------: | -------------: |
| CRM          | Phone = `0901234567`  |              1.0 |           1.0 |         0.95 |     1.0 |       **0.95** |
| Web Survey   | Email = `a@gmail.com` |              1.0 |           0.9 |         0.70 |     0.9 |      **0.567** |
| Facebook Ads | Email = `a@gmail.com` |              1.0 |           0.9 |         0.40 |     0.8 |      **0.288** |

So:

[
E_{CRM}=1.0\times1.0\times0.95\times1.0=0.95
]

[
E_{Survey}=1.0\times0.9\times0.70\times0.9=0.567
]

[
E_{Facebook}=1.0\times0.9\times0.40\times0.8=0.288
]

### Diễn giải

Điểm quan trọng là **cùng một identity signal không có cùng evidential value giữa các source**.

> **CRM phone match = bằng chứng mạnh** vì CRM là first-party source có độ tin cậy cao.
> **Web Survey email match = bằng chứng vừa phải** vì người dùng tự nhập dữ liệu, nhưng dữ liệu có thể có lỗi.
> **Facebook Ads email match = bằng chứng yếu hơn** vì dữ liệu có thể được suy ra, hash, upload hoặc được kiểm soát ít trực tiếp hơn.

Ví dụ, nếu Facebook Ads cho biết email khớp nhưng CRM cho biết số điện thoại
thuộc về một người khác, hệ thống **không nên xem hai signal có giá trị ngang
nhau**. Bằng chứng từ CRM có trọng số lớn hơn đáng kể.

Một cách rất đơn giản để giải thích CIR là:

> **CIR không chỉ hỏi “Các giá trị này có khớp không?” mà hỏi “Match mạnh đến đâu, source đáng tin cậy thế nào và dữ liệu tốt đến mức nào?”**

\newpage

# 12. Dynamic Identity Matching

CIR không nên yêu cầu mọi raw profile phải chứa cùng một tập thuộc tính.

Thay vào đó, CIR xác định động những identity signal nào đang có.

Với hai profile:

$$
r_i =
{device_id,email,phone}
$$

$$
r_j =
{device_id,email}
$$

các identity signal chung là:

$$
S(r_i,r_j)
=
{device_id,email}
$$

CIR chỉ đánh giá các signal phù hợp.

Trong hệ thống, điều này có thể được biểu diễn như sau:

```text
DYNAMICMATCH:
device_id
email
```

Cách tiếp cận dynamic quan trọng vì các hệ thống khác nhau vốn cung cấp những
thông tin nhận dạng khác nhau.


# 13. Evidence Score và Calibrated Probability

Với mỗi cặp identity candidate, CIR trước hết tính evidence-support score. Một
công thức weighted đơn giản là:

$$
S(r_i,r_j) =
\sum_{k \in K_{obs}}
M_k
\cdot
W_{signal,k}
\cdot
W_{source,k}
\cdot
W_{quality,k}
\cdot
W_{independence,k}
\cdot
W_{time,k}
$$

trong đó $K_{obs}$ chứa các phép so sánh có thể sử dụng, còn các factor bổ sung
được định nghĩa khi mô hình hóa temporal decay hoặc correlated evidence. Không
được xem giá trị thiếu là agreement. High-quality evidence mâu thuẫn phải làm
giảm score hoặc block automatic link.

Score $S$ không tự động là calibrated probability. Nếu hệ thống cần báo cáo một
probability, hệ thống nên ước tính nó từ các cặp cùng người và khác người đã gắn
nhãn, chẳng hạn bằng supervised model:

$$
\operatorname{logit}(\hat{p})
=
\beta_0 + \sum_k \beta_k x_k
$$

trong đó các feature $x_k$ gồm comparison outcome, signal type, source context,
data quality, recency và candidate ambiguity. Giá trị
$\hat{p}=P(Y=1\mid x)$ thu được phải được calibrate trên held-out data và theo
dõi drift. Nếu không có label đại diện, hệ thống nên báo cáo evidence score và
độ bất định của nó thay vì gọi score đó là probability.

Calibrated probability, khi có, thỏa mãn:

$$
\hat{p} \in [0,1]
$$

và chỉ có thể biểu diễn dưới dạng phần trăm sau khi calibration:

$$
Confidence = 100\hat{p}
$$


## 13.1 Ví dụ: Evidence từ CRM nội bộ

Giả sử CRM nội bộ cung cấp một số điện thoại.

```text
Source Trust
= 0.90

Phone Signal Strength
= 0.95

Data Quality
= 1.00
```

Therefore:

$$
E =
0.95 \times 0.90 \times 1.00
$$

$$
E = 0.855
$$

Đây là identity evidence mạnh.


## 13.2 Ví dụ: External Feedback Survey

Giả sử khách hàng tự nguyện nhập cùng số điện thoại vào một web feedback survey bên ngoài.

```text
Source Trust
= 0.20

Phone Signal Strength
= 0.95

Data Quality
= 1.00
```

Therefore:

$$
E =
0.95 \times 0.20 \times 1.00
$$

$$
E = 0.19
$$

Số điện thoại vẫn là một **loại identifier mạnh**, nhưng evidence contribution
của nó thấp vì source kém đáng tin cậy hơn.

Đây là một phân biệt nền tảng:

> **Một identifier mạnh từ source yếu không nhất thiết là identity evidence mạnh.**

\newpage 

# 14. Nhiều Evidence Source

CIR nên cho phép evidence tích lũy qua các source độc lập.

Consider:

```text
CRM
phone = 0901234567
trust = 0.90

Mobile App
device_id = A123
trust = 0.85

Web Survey
phone = 0901234567
trust = 0.20
```

Có thể biểu diễn evidence như sau:

```text
CRM Phone
#################  0.855

App Device
##########        0.510

Survey Phone
####               0.190
```

Evidence từ survey không bị loại bỏ.

Nó chỉ đóng góp ít hơn vào quyết định nhận dạng.

Do đó:

> **Evidence yếu có thể củng cố một identity hypothesis, nhưng hiếm khi nên tự nó xác lập identity.**


# 15. Independence của Evidence

CIR cũng nên xem xét liệu nhiều signal có thực sự độc lập hay không.

Ví dụ:

```text
email
phone
```

có thể cung cấp hai identity signal khác nhau.

Tuy nhiên:

```text
device_id
cookie_id
```

có thể cùng bắt nguồn từ một browser hoặc device, vì vậy không nhất thiết được
xem là evidence hoàn toàn độc lập.

Do đó, production CIR system không nên chỉ đơn giản cộng mọi matching signal.

Một model tinh vi hơn có thể đưa vào independence factor hoặc correlation factor:

$$
E_k =
M_k
\cdot
W_{signal,k}
\cdot
W_{source,k}
\cdot
W_{quality,k}
\cdot
W_{independence,k}
$$

trong đó:

$$
W_{independence,k} \in [0,1]
$$

giảm việc double-count các correlated signal.

Factor này là một modeling assumption, không phải bằng chứng rằng evidence độc
lập. Hệ thống nên nhóm các signal có chung nguồn gốc, giới hạn combined
contribution của chúng và kiểm thử hiệu năng trên các bản ghi từ cùng household,
device, network hoặc account. Các bản sao lặp lại của cùng giá trị từ các feed
khác nhau không nên được tính là những confirmation độc lập.


# 16. Resolution Threshold

Sau khi CIR tính evidence score hoặc calibrated probability, kết quả có thể
được phân loại thành các resolution band.

Ví dụ:

```text
Score hoặc calibrated probability
    |
    +++ 0.90 +++++++++ AUTO LINK
    |
    +++ 0.70 +++++++++ REVIEW
    |
    ++++ 0.00 +++++++++ NO MATCH
```

  Các phương trình dưới đây giả định $\hat{p}$ là calibrated probability. Nếu
  chỉ có raw score $S$, hệ thống phải dùng các score threshold được validate
  riêng và không được gắn nhãn chúng là probability.

  Về mặt khái niệm:

$$
\hat{p} \geq T_{auto}
\Rightarrow
LINK \quad \text{if no blocking conflict exists}
$$

$$
T_{review} \leq \hat{p} < T_{auto}
\Rightarrow
REVIEW
$$

$$
\hat{p} < T_{review}
\Rightarrow
NO\ LINK
$$

Automatic link cũng nên xét chênh lệch giữa candidate tốt nhất và tốt thứ hai,
hard contradiction và chi phí của false positive. Threshold nên được chọn bằng
labeled validation set cùng các mục tiêu rõ ràng về precision, recall, coverage
và review capacity. Các giá trị như 0.90 và 0.70 chỉ là ví dụ; chúng không phải
probability hợp lệ trong mọi trường hợp.

Với identity operation có rủi ro cao, tổ chức có thể yêu cầu evidence mạnh hơn.

Với use case personalization rủi ro thấp, threshold thấp hơn có thể chấp nhận được.


# 17. Resolution Outcome

CIR nên phân biệt **confidence** và **resolution outcome**.

Các outcome có thể gồm:

### 17.1 Master Profile hiện có

Có candidate match đủ bằng chứng và không mơ hồ:

$$
r_i \rightarrow M_j
$$

### 17.2 Review

Evidence chưa đủ để resolution tự động:

$$
r_i \rightarrow REVIEW
$$

### 17.3 No Match

Raw profile không được liên kết với candidate đang đánh giá, vì evidence chưa
đủ hoặc contradiction block link:

$$
r_i \rightarrow NO\ MATCH
$$

### 17.4 Master Profile mới

Không có candidate hiện có nào phù hợp được chọn và hệ thống tạo một identity
cluster hoặc Master Profile provisional mới theo lifecycle policy:

$$
r_i \rightarrow M_{new}
$$


# 18. Master Profile mới không phải là Match 100%

Phân biệt này đặc biệt quan trọng khi thiết kế CIR system.

Giả sử một raw profile mới xuất hiện:

```text
device_id = A123
```

và CIR không tìm thấy Master Profile phù hợp.

Hệ thống tạo:

```text
Master Profile M1005
```

Điều này **không** có nghĩa raw profile đã được chứng minh là đại diện cho một
người mới trong thế giới thực. Nó cũng không tạo ra match probability cho một
candidate hiện có; giá trị đó phải để unavailable hoặc đánh dấu rõ là không
áp dụng.

Thay vào đó, cách diễn giải đúng là:

```text
Resolution Outcome:
NEW_MASTER

Match Probability:
N/A
```

Master Profile mới là một operational container hoặc identity hypothesis. Về
sau, nó có thể được link, split, merge hoặc retire khi có evidence mới. Hệ thống
nên giữ candidate-level decision, lifecycle action và mọi probability hoặc score
ở các field riêng biệt.

Vì vậy, hệ thống nên duy trì các field riêng biệt:

```text
resolution_outcome
match_score
calibrated_match_probability
match_reason
match_evidence
```

thay vì gộp chúng thành một giá trị duy nhất.


# 19. Mô hình Identity Graph Link

Vì vậy, một identity link trong production có thể được mô hình hóa như sau:

$$
\begin{aligned}
L = \{&\text{raw\_profile\_id},\ \text{master\_profile\_id},\ \text{evidence\_score},\\
&\text{calibrated\_match\_probability},\ \text{outcome},\ \text{evidence},\\
&\text{source\_context},\ \text{timestamp},\ \text{algorithm\_version}\}
\end{aligned}
$$

Ví dụ:

```text
Raw Profile
759301f2...

Master Profile
M000128

Calibrated Match Probability (minh họa)
0.96

Outcome
LINKED

Evidence
device_id
email
phone_number
external_customer_id

Sources
Mobile App
Website
CRM

Algorithm Version
cir-v2.4

Timestamp
2026-08-24
```

Điều này tạo ra provenance đầy đủ.


# 20. Explainability

Mọi quyết định CIR đều phải explainable.

Thay vì chỉ trả về:

```text
MATCH = TRUE
```

CIR nên trả về:

```text
MATCH = TRUE

CONFIDENCE = 0.96

REASON:
Matched using:
- email
- phone
- device_id

SOURCE EVIDENCE:
CRM = high trust
Mobile App = high trust
Website = medium trust
```

Điều này cho phép business user, data engineer và auditor hiểu vì sao một quan
hệ nhận dạng tồn tại.


\newpage

# 21. Temporal Identity

Identity không nhất thiết cố định.

Một người có thể:

* đổi số điện thoại;
* đổi địa chỉ email;
* thay device;
* ngừng sử dụng application;
* dùng chung household device;
* mất quyền truy cập account.

Vì vậy, identity evidence nên bao gồm thời gian:

$$
E_k(t)
$$

Một số điện thoại mới được verify có thể phù hợp hơn số điện thoại cũ.

Có thể đưa vào temporal decay factor khi kỳ vọng evidence lịch sử kém khả năng
dự báo hơn. Điều này không phải lúc nào cũng phù hợp: device ID có thể chỉ tồn
tại ngắn hạn, trong khi contractual customer identifier có thể còn hiệu lực
cho đến khi bị revoke rõ ràng. Quy tắc expiration, revocation và
effective-validity nên được mô hình hóa tách biệt với statistical decay.

$$
W_{time,k}(\Delta t)
 =
e^{-\lambda_k \Delta t}
$$

trong đó:

* ($\Delta t$) = tuổi của evidence;
* ($\lambda_k$) = decay rate theo từng signal.

Evidence model mở rộng là:

$$
\boxed{
E_k =
M_k
\cdot
W_{signal,k}
\cdot
W_{source,k}
\cdot
W_{quality,k}
\cdot
W_{independence,k}
\cdot
W_{time,k}
}
$$

Điều này cho phép CIR phân biệt current identity evidence và historical identity
evidence. Decay parameter nên được estimate hoặc approve cho từng signal và
validate trên time-sliced data. Decay không được âm thầm xóa provenance lịch sử
hoặc override một revocation rõ ràng.


# 22. Adaptive Source Trust

Source reliability không nhất thiết phải cố định.

Let:

$$
T_s(t)
$$

đại diện cho trust score của source (s) tại thời điểm (t).

Có thể dùng historical validation để update score.

Nếu một source liên tục tạo ra identity information được gắn nhãn chính xác:

$$
T_s(t+1) > T_s(t)
$$

Nếu một source tạo ra nhiều match bị gắn nhãn sai:

$$
T_s(t+1) < T_s(t)
$$

Điều này có thể hỗ trợ adaptive identity-resolution system, nhưng update không
được chỉ học từ quyết định của CIR hoặc business outcome chưa verify. Các
signal đó chịu selection bias và có thể củng cố lỗi hiện có. Trust update nên
dùng label được verify độc lập, sample size tối thiểu, confidence interval,
approval control, policy change có version và rollback path.

```text
Historical Resolution Outcomes
             |
             v
       Source Accuracy
             |
             v
        Source Trust
             |
             v
      CIR Evidence Model
             |
             v
      Score hoặc Match Probability
             |
             v
      Identity Graph
```

Điều này tạo ra feedback loop giữa data quality và identity resolution.

\newpage 

# 23. Identity Resolution Pipeline

Quy trình CIR hoàn chỉnh có thể được biểu diễn như sau:

```text
                     RAW CUSTOMER DATA
                            |
             ----------------------------------
             v              v              v
       Ads Data Sources   Website          App
       Google Ads         Web Events       Mobile SDK
       TikTok             Login            User ID
       Facebook Ads       Forms            Device ID
             |              |              |
             ----------------------------------
                            v
                           CIR
              Customer Identity Resolution
                            |
                            v
                Identity Signal Extraction
                            |
                            v
                  Candidate Generation
                            |
                            v
                 Dynamic Signal Matching
                            |
                  ------------------------
                  v         v         v
                Email      Phone    Device ID
                  |         |         |
                  ------------------------
                            v
                  Source Trust Evaluation
                            |
                            v
                    Data Quality Check
                            |
                            v
                   Evidence Aggregation
                            |
                            v
                   Match Confidence
                            |
                ----------------------------
                v           v           v
             LINK         REVIEW      NO MATCH
                |
                v
          Existing Master
             Profile

              OR

          New Master Profile
```

\newpage

# 24. Diễn giải kiến trúc

Vì vậy, có thể xem CIR là decision layer giữa raw data ingestion và Customer 360.

```text
DATA SOURCES
     |
     v
RAW DATA
     |
     v
IDENTITY RESOLUTION
     |
     +++ Identity Signals
    +++ Source Reliability
     +++ Data Quality
     +++ Temporal Evidence
    ++++ Score hoặc Match Probability
     |
     v
IDENTITY GRAPH
     |
     v
MASTER PROFILES
     |
     v
CUSTOMER 360
     |
     +++ Segmentation
     +++ Personalization
     +++ Customer Journey
     +++ CLV
     +++ Predictive AI
     ++++ Marketing Activation
```

Kiến trúc này giúp downstream application không phải tự giải quyết customer identity.


# 25. Quan hệ với Customer 360

Customer 360 phụ thuộc nhiều vào identity quality, nhưng identity resolution
chỉ là một trong các yếu tố đóng góp vào downstream quality.

Nếu CIR merge nhầm hai người:

$$
Person_A + Person_B
\rightarrow
Wrong\ Master\ Profile
$$

mọi analytical và marketing process downstream đều có thể bị ảnh hưởng.

Ví dụ:

* purchase history bị sai;
* CLV bị sai;
* segmentation bị sai;
* recommendation bị sai;
* lead score bị sai;
* personalization bị sai.

Ingestion failure, attribute lỗi thời, consent không đầy đủ, transformation bug,
measurement error và downstream business logic cũng có thể làm giảm chất lượng
Customer 360. Vì vậy CIR không chỉ là một infrastructure component.

Đó là một **foundational data-quality layer cho Customer 360**.


# 26. Identity Resolution Metadata trong Customer 360

Hệ thống nên lưu giữ evidence và decision status của từng identity relationship.
Một aggregate confidence value duy nhất cho Master Profile có thể che giấu
conflict giữa các link và không nên thay thế chi tiết ở cấp link.

Ví dụ:

```text
Master Profile
------------------------------
master_id: M000128

Identity Resolution Summary:
link_status: active
conflict_status: none
last_evaluated_at: 2026-08-24

Linked Profiles:
4

Trusted Sources:
CRM
Mobile App
Website

Evidence:
email
phone
device_id
customer_id
```

Mỗi linked profile nên giữ score hoặc calibrated probability, evidence, validity
period và model/policy version riêng. Summary có thể giúp downstream system đưa
ra quyết định risk-aware, nhưng phải định nghĩa cách suy ra summary và không
được diễn giải là probability nếu chưa calibration.

Ví dụ:

```text
Calibrated probability cao và permitted purpose
 personalized marketing

Medium calibrated probability
 less sensitive personalization

Calibrated probability thấp hoặc unresolved conflict
 anonymous experience
```

Do đó, identity-resolution evidence có thể trở thành feature hữu ích cho
downstream decision system, với điều kiện score, calibration status, validity
và permitted purpose được nêu rõ.

\newpage 

# 27. Identity Graph so với Deduplication truyền thống

Deduplication truyền thống hỏi:

> Hai bản ghi này có phải là bản trùng lặp không?

CIR hỏi:

> Evidence nào cho thấy các bản ghi này đại diện cho cùng một người trong thế giới thực?

Deduplication truyền thống thường tạo ra:

```text
Record A
Record B
      
MERGE
```

CIR tạo ra:

```text
Record A
      |
      + evidence
      + confidence
      + provenance
      v
Master Profile
      ^^
      + evidence
      + confidence
      + provenance
      |
Record B
```

Cách thứ hai phù hợp hơn với môi trường CDP liên tục thay đổi.


# 28. Các nguyên tắc phương pháp luận

Phương pháp CIR đề xuất dựa trên bảy nguyên tắc.

### Nguyên tắc 1 - Identity mang tính xác suất

Identity resolution nên thừa nhận sự bất định thay vì giả định mọi match đều deterministic.

### Nguyên tắc 2 - Evidence có độ mạnh khác nhau

Email, phone, device ID và anonymous ID không nên tự động nhận weight ngang nhau.

### Nguyên tắc 3 - Source có mức độ tin cậy khác nhau

Một bản ghi CRM nội bộ đã verify nhìn chung nên đóng góp nhiều evidence hơn một external survey ẩn danh.

### Nguyên tắc 4 - Data quality có ý nghĩa

Ngay cả source đáng tin cậy cũng có thể chứa dữ liệu invalid, stale, incomplete hoặc inconsistent.

### Nguyên tắc 5 - Evidence phải explainable

Mọi identity relationship phải cung cấp reason và provenance.

### Nguyên tắc 6 - Identity phải mang tính thời gian

Identity evidence thay đổi theo thời gian, vì vậy nên giữ timestamp và decay khi phù hợp.

### Nguyên tắc 7 - Identity nên được biểu diễn dưới dạng graph

Hệ thống nên lưu giữ quan hệ giữa raw profile, identity signal, source và Master Profile thay vì đơn giản hủy các bản ghi nguồn bằng những lần merge không thể đảo ngược.


# 29. CIR Data Model đề xuất

Quan hệ logic

```text
SOURCE REGISTRY
       |
       +-------------------+
       |                   |
       v                   v
 RAW PROFILE       IDENTITY EVIDENCE
       |                   ^
       |                   |
       v                   |
 IDENTITY LINK -----------+
       |
       v
 MASTER PROFILE
```

---

Một CIR data model khái niệm có thể gồm:

```text
cir_master_profile
------------------------
master_profile_id
identity_resolution_summary
created_at
updated_at
status


cir_raw_profile
------------------------
raw_profile_id
source_id
source_record_id
attributes
created_at
updated_at


cir_identity_link
------------------------
link_id
raw_profile_id
master_profile_id
match_score
calibrated_match_probability
resolution_outcome
match_reason
algorithm_version
created_at
updated_at


cir_identity_evidence
------------------------
evidence_id
link_id
signal_type
signal_value_hash
signal_weight
source_id
source_reliability
data_quality
time_weight
evidence_weight
observed_at


cir_source_registry
------------------------
source_id
source_name
source_type
trust_weight
quality_score
status
updated_at
```

Mô hình này tách biệt:

* raw data;
* Master Profile;
* identity link;
* evidence;
* source governance.

# 30. Ví dụ End-to-End Resolution

Consider the following records.

### Website

```text
device_id = A123
email = customer@example.com
```

### Mobile App

```text
device_id = A123
phone = 0901234567
```

### CRM

```text
customer_id = C001
email = customer@example.com
phone = 0901234567
```

### Facebook Ads

```text
campaign_id = FB-C100
ad_id = FB-A200
click_id = CL123
email = customer@example.com
```

Bản ghi Facebook cung cấp **supporting identity evidence** vì có email. Một
Facebook Ad impression hoặc click thông thường không có identity attribute thì
chủ yếu là behavioral evidence.

### Feedback Survey

```text
phone = 0901234567
```

CIR quan sát:

```text
                       CUSTOMER IDENTITY
                              |
              +---------------+---------------+
              |               |               |
             CRM           Website           App
              |               |               |
              |               |               |
          customer_id       email         device_id
              |               |               |
              |               +-------+-------+
              |                       |
              +-----------+-----------+
                          |
                        phone
                          |
              +-----------+-----------+
              |                       |
          Survey                  Facebook Ads
          phone                    email
              |                       |
              +-----------+-----------+
                          |
                          v
                        CIR
                          |
                          v
                   Evidence Model
                          |
              +-----------+-----------+
              |           |           |
            Signal      Source      Quality
            Weight      Trust        Score
              |           |           |
              +-----------+-----------+
                          |
                          v
                   Match Confidence
                          |
                          v
                 Existing Master Profile
```

Evidence từ CRM nhận trust cao.

Mobile application cung cấp supporting identity evidence thông qua device ID và
số điện thoại dùng chung.

Website cung cấp supporting evidence thông qua email và device ID.

Bản ghi Facebook Ads cung cấp identity evidence yếu hơn khi có email hoặc
identifier có thể sử dụng; riêng một ad click không nên được xem là bằng chứng
về danh tính con người.

Survey cung cấp evidence yếu hơn vì số điện thoại do người dùng tự khai báo.

Evidence kết hợp có thể tạo ra identity relationship có confidence cao đồng thời
giữ lại mức trust khác nhau của từng source.



# 31. Các cân nhắc về Security và Privacy

CIR xử lý identity information nên cần các privacy và security control phù hợp.

Thông thường không nên lưu trữ hoặc so sánh identity value ở dạng plaintext khi
không cần thiết. Plain deterministic hash không phải anonymization: email và số
điện thoại có domain hữu hạn, có thể đoán được và chịu dictionary attack.

Ví dụ:

$$
email
\rightarrow
\mathrm{normalize(email)}
\rightarrow
\mathrm{keyed\ HMAC\ or\ token}
$$

và:

$$
phone
\rightarrow
\mathrm{normalize(phone)}
\rightarrow
\mathrm{keyed\ HMAC\ or\ token}
$$

Keyed comparison token cần key management được bảo vệ, quy trình rotation,
access control và cách xử lý cẩn trọng khi nhiều tổ chức hoặc tenant cần
interoperate. Encryption at rest hoặc in transit tự nó không thay thế được
authorization, retention, deletion và purpose control.

Hệ thống cũng nên triển khai, theo yêu cầu của luật và policy áp dụng:

* access control;
* encryption;
* audit logging;
* data minimization;
* retention policy;
* consent management;
* purpose limitation;
* PII protection;
* tenant và purpose isolation;
* quy trình data-subject access và deletion.

Vì vậy, identity graph chỉ nên lưu giữ lượng thông tin tối thiểu cần thiết để
thiết lập và duy trì identity relationship.


# 32. Operational Monitoring

Production CIR system nên liên tục monitor:

### Identity quality

* match rate;
* false-positive rate và false-negative rate, được đo trên sample đã gắn nhãn
  hoặc adjudicate và có document;
* unresolved profile rate;
* duplicate Master Profile rate.

### Source quality

* source trust;
* source completeness;
* source freshness;
* source validation rate.

### Resolution behavior

* score calibration và uncertainty;
* tỷ lệ automatic link;
* tỷ lệ review case;
* tỷ lệ Master Profile mới;
* thay đổi identity link theo thời gian.

Không thể suy ra false-positive rate và false-negative rate chỉ từ số lượng
match. Evaluation set nên bao gồm hard negative, difficult positive, ví dụ chia
theo thời gian và các population liên quan. Tổ chức cũng nên monitor calibration,
candidate coverage, cluster-size distribution, review outcome và drift trong
hành vi của signal và source.

\newpage 

# 33. Feedback Loop

CIR có thể vận hành như một controlled learning system, nhưng business outcome
không tự động là identity label.

```text
                 CIR DECISION
                      |
                      v
                Master Profile
                      |
                      v
              Business Outcomes
                      |
       ----------------------------------
       v              v              v
    Purchase       Login         Customer
    Confirmed      Confirmed      Service
       |              |              |
       ----------------------------------
                      v
               Identity Validation
                      |
                      v
                Update Trust
                      |
                      v
                    CIR
```

Ví dụ, nếu identity được cho là đã match về sau authentication bằng một
customer account đã verify khác, sự kiện đó có thể là evidence cho thấy
resolution trước đó không chính xác, nhưng vẫn phải xét account sharing,
account takeover và các khả năng khác. Label chất lượng cao nên đến từ verified
account relationship, adjudicated review, confirmed dispute hoặc các event
được validate độc lập khác.

Controlled feedback process là:

$$
\text{Identity Resolution}
\rightarrow
\text{Validation}
\rightarrow
\text{Learning}
\rightarrow
\mathrm{Improved\ Resolution}
$$

Training và policy update phải được đánh giá trên held-out data trước khi
deployment, với versioned rollout và rollback control. Hệ thống không được
update source trust chỉ từ các link trước đó của chính nó, vì điều này có thể
củng cố false match.

# 34. Mô hình lý thuyết

SAE-CIR evidence model hoàn chỉnh có thể tóm tắt như sau:

$$
\boxed{
S(r_i,r_j)
=
\sum_{k \in K_{obs}}
M_k
\cdot
W_{signal,k}
\cdot
W_{source,k}
\cdot
W_{quality,k}
\cdot
W_{independence,k}
\cdot
W_{time,k}
}
$$

trong đó:

* $M_k$ = comparison result của identity signal $k$;
* $W_{signal,k}$ = độ mạnh nội tại của identity signal;
* $W_{source,k}$ = độ tin cậy theo source và context;
* $W_{quality,k}$ = chất lượng của dữ liệu được quan sát;
* $W_{independence,k}$ = điều chỉnh cho correlated evidence;
* $W_{time,k}$ = mức độ liên quan theo thời gian của evidence.

Với mỗi identity signal được quan sát:

$$
\boxed{
E_k =
M_k
\cdot
W_{signal,k}
\cdot
W_{source,k}
\cdot
W_{quality,k}
\cdot
W_{independence,k}
\cdot
W_{time,k}
}
$$

Do đó, tổng evidence score là tổng các evidence contribution riêng lẻ:

$$
S(r_i,r_j)=\sum_k E_k
$$

Score $S$ là một **evidence score, không phải probability**. Giá trị của nó
phụ thuộc vào weight được chọn và evidence sẵn có.

### Ví dụ

Giả sử CIR đánh giá liệu một raw profile có thuộc về Master Profile hiện có hay không.

| Source | Signal | $M_k$ | $W_{signal}$ | $W_{source}$ | $W_{quality}$ | $W_{independence}$ | $W_{time}$ | $E_k$ |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| CRM | Phone | 1.00 | 0.95 | 0.95 | 1.00 | 1.00 | 1.00 | **0.903** |
| Web Survey | Phone | 1.00 | 0.95 | 0.30 | 0.90 | 1.00 | 1.00 | **0.257** |
| FB Lead Ad | Phone | 1.00 | 0.95 | 0.40 | 0.85 | 1.00 | 1.00 | **0.323** |

Therefore:

$$
S
=
0.903+0.257+0.323
=
\boxed{1.483}
$$

Ví dụ này cho thấy cùng một phone match có thể đóng góp lượng evidence khác nhau
vì các observation đến từ những source có mức trust và data quality khác nhau.

Score có thể được dùng trực tiếp cho một rule-based resolution policy đã
validate. Khi có các cặp cùng người và khác người đã gắn nhãn mang tính đại
diện, các evidence feature có thể được calibration thành match probability:

$$
\operatorname{logit}(\hat{p})
=
\beta_0+\sum_k\beta_kx_k
$$

trong đó:

$$
\hat{p}=P(Y=1\mid x)
$$

và $x_k$ có thể gồm comparison result, signal type, source context, data
quality, independence, recency và candidate ambiguity.

Nếu không có labeled data mang tính đại diện, CIR nên báo cáo evidence score $S$
thay vì trình bày nó như một probability.

Khi đó, resolution decision cuối cùng về mặt khái niệm là:

$$
Decision(\hat{p})=
\begin{cases}
LINK & \hat{p}\geq T_{auto}\\
REVIEW & T_{review}\leq\hat{p}<T_{auto}\\
NO\ LINK & \hat{p}<T_{review}
\end{cases}
$$

Nếu không tìm thấy Master Profile hiện có phù hợp, hệ thống có thể tạo một
Master Profile provisional mới theo lifecycle policy.

\newpage

# 35. Kiến trúc CIR thực tiễn

Một production implementation có thể được tổ chức thành các logical component sau:

\begin{figure}[h]
\centering
\begin{verbatim}
-------------------------------------------------
+              DATA SOURCES                     +
|                                               |
+ Ads + Website + App + CRM + POS + Surveys     +
--------------------------------------------------
                      |
                      v
-------------------------------------------------
+             RAW PROFILE STORE                 +
--------------------------------------------------
                      |
                      v
-------------------------------------------------
+          IDENTITY SIGNAL EXTRACTION           +
|                                               |
+ Email + Phone + Device + Customer ID + ...    +
-------------------------------------------------
                      |
                      v
-------------------------------------------------
+             CIR ENGINE                        +
|                                               |
+ Candidate Generation                          +
+ Dynamic Matching                              +
+ Source Trust                                  +
+ Data Quality                                  +
+ Evidence Aggregation                          +
+ Confidence Estimation                         +
-------------------------------------------------
                      |
                      v
-------------------------------------------------
+             IDENTITY GRAPH                    +
|                                               |
+ Raw Profiles + Master Profiles                +
+ Evidence + Confidence + Provenance            +
-------------------------------------------------
                      |
                      v
-------------------------------------------------
+             CUSTOMER 360                      +
|                                               |
+ Profile + Journey + CLV + Segments + AI       +
-------------------------------------------------
\end{verbatim}
\end{figure}

\newpage 

# 36. Kết luận

Customer Identity Resolution nên được hiểu là một **evidence-based identity
inference system**, thay vì một record-matching hoặc database-deduplication
mechanism đơn giản.

Phương pháp Source-Aware Evidence-Based CIR đề xuất đánh giá identity evidence
bằng ba chiều cốt lõi:

$$
\boxed{
\text{Evidence}
=
\text{Signal Strength}
\times
\text{Source Reliability}
\times
\text{Data Quality}
}
$$

Khi cần, mô hình có thể mở rộng với independence factor và temporal factor:

$$
\boxed{
\text{Evidence}
=
\text{Signal}
\times
\text{Source}
\times
\text{Quality}
\times
\text{Independence}
\times
\text{Time}
}
$$

Các evidence contribution thu được được aggregate để đánh giá raw profile nên
được link với Master Profile hiện có, đưa đi review hay giữ ở trạng thái
unresolved.

Phương pháp này thừa nhận một thực tế quan trọng của customer data:

> **Không phải mọi customer information đều có evidential value như nhau.**

Một bản ghi CRM nội bộ đã verify có thể cung cấp evidence mạnh, trong khi một
giá trị tự khai báo từ web survey hoặc marketing platform có thể cung cấp
evidence yếu hơn. Vì vậy, evidence được đánh giá theo cả identity signal và
context nơi nó được quan sát.

Identity Graph thu được lưu giữ:

* identity evidence;
* source provenance;
* data quality;
* evidence score hoặc calibrated probability;
* temporal context;
* resolution outcome.

Do đó CIR trở thành nhiều hơn một matching mechanism. CIR cung cấp một
**identity layer explainable và auditable** cho Customer 360.

Phương pháp có thể được tóm tắt như sau:

$$
\boxed{
\text{Detect}
\rightarrow
\text{Evaluate}
\rightarrow
\text{Resolve}
\rightarrow
\text{Learn}
}
$$

Mục tiêu thực tiễn không phải tuyên bố sự chắc chắn khi nó không tồn tại, mà là
biến mọi quyết định nhận dạng thành **measurable, explainable và có thể xem xét
lại khi có evidence mới**.