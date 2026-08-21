# Persona as a Vector: Từ Customer 360 đến Customer Transformation

**Subtitle:** Một lý thuyết Attractor về Human Identity, Personalization và Transformation  
**Dựa trên paper:** "Persona as a Vector" — Trieu Nguyen (LEOCDP.com)  
**Thời lượng đề xuất:** 10–12 phút  
**Tone:** tự nhiên, chuyên môn nhưng dễ hiểu  
**Đối tượng:** Marketing, Product, Data, AI, Business Leaders

---

## OPENING — 0:00–0:45

Có một câu hỏi mà mình nghĩ marketing sẽ phải thay đổi trong thời đại AI.

Trước đây chúng ta thường hỏi:

> **“Khách hàng này sẽ mua gì?”**

Sau đó, với CDP và Customer 360, câu hỏi trở thành:

> **“Khách hàng này là ai?”**

Nhưng mình nghĩ câu hỏi tiếp theo phải sâu hơn:

> **“Khách hàng này đang là ai, họ muốn trở thành ai, và chúng ta có thể làm gì để hỗ trợ họ đi đến trạng thái đó?”**

Đó chính là ý tưởng mình muốn trình bày trong paper **“Persona as a Vector”**.

Một framework kết hợp **Customer 360, Deep Learning, Persona Conversion Scoring và Generative AI** để nhìn personalization không chỉ như một bài toán conversion, mà như một bài toán **customer transformation**.

---

# 1. Vấn đề của Persona truyền thống — 0:45–1:45

Trong marketing, chúng ta đã quá quen với Persona.

Ví dụ:

> Female, 30–40 tuổi, sống ở thành phố, thu nhập cao, thích shopping.

Hoặc:

> Premium Customer.

Hoặc:

> High-value Customer.

Những label này rất hữu ích.

Nhưng vấn đề là:

**Con người không đứng yên.**

Needs thay đổi.

Intent thay đổi.

Behavior thay đổi.

Context thay đổi.

Và quan trọng nhất:

**Aspiration cũng thay đổi.**

Một người hôm nay có thể là một người chưa tập thể dục.

Nhưng họ muốn trở thành một người khỏe mạnh.

Một người hôm nay có thể lo lắng về tài chính.

Nhưng họ muốn trở thành một người Financially Confident.

Một người hôm nay có thể chưa hiểu sản phẩm.

Nhưng họ muốn trở thành một người tiêu dùng hiểu biết và tự tin.

Vì vậy, paper đưa ra một premise rất đơn giản:

> **Persona is not merely a label. Persona is a state in transition.**

Hay nói bằng tiếng Việt:

> **Persona không chỉ là một nhãn mô tả khách hàng. Persona là một trạng thái đang thay đổi.**

Đây là nền tảng của toàn bộ framework.

---

# 2. Từ Customer as Target đến Customer as Dynamic Persona — 1:45–2:30

Nếu nhìn lại quá trình phát triển của marketing, chúng ta có thể hình dung:

**Customer as Target**

→ **Customer as Profile**

→ **Customer as Dynamic Persona**

Ngày trước, chúng ta target một nhóm người.

Sau đó chúng ta xây dựng Customer Profile.

Rồi đến CDP, chúng ta có Customer 360.

Nhưng bước tiếp theo, theo mình, là:

> **Customer as a Dynamic Persona.**

Customer không còn chỉ là một record trong database.

Customer trở thành một **state** trong một không gian hành vi và identity.

Và khi customer thay đổi state, Persona cũng thay đổi theo.

---

# 3. Persona as a Vector là gì? — 2:30–3:30

Đây là phần cốt lõi của paper.

Thay vì nói:

> Customer này thuộc Persona A.

Chúng ta biểu diễn Persona bằng một **Vector**.

Ví dụ:

**Values**

**Behavior**

**Needs**

**Intent**

**Emotion**

**Aspiration**

**Social Influence**

Những dimension này tạo thành một **Persona State Vector**.

Ví dụ, một customer có thể hiện tại:

- Product Interest: cao
- Purchase Intent: trung bình
- Content Engagement: cao
- Price Confidence: thấp
- Aspiration về financial security: cao

Đây không phải là một label.

Đây là một **state**.

Và state này có thể thay đổi theo thời gian.

Paper cũng nhấn mạnh rằng đây chỉ là một representation có tính ước lượng, không phải một cách “đo” toàn bộ con người bằng vài con số.

---

# 4. Current Persona và Desired Persona — 3:30–4:20

Khi đã có Current Persona, chúng ta có một câu hỏi rất thú vị:

> **Customer muốn trở thành ai?**

Paper gọi đó là:

**Desired Persona.**

Ví dụ:

**Financially Anxious**

→ **Financially Confident**

Hoặc:

**Sedentary**

→ **Active**

Hoặc:

**Novice Consumer**

→ **Knowledgeable Consumer**

Và giữa Current Persona và Desired Persona tồn tại một khoảng cách.

Paper gọi khoảng cách này là:

> **Transformation Gap.**

Nói đơn giản:

**Customer đang ở đâu?**

và

**Customer muốn đi đâu?**

Khoảng cách giữa hai trạng thái chính là nơi personalization bắt đầu có ý nghĩa.

Và Desired Persona đóng vai trò như một **attractor** — mượn ý tưởng từ dynamical systems.

Không phải một lực hấp dẫn vật lý theo nghĩa đen.

Mà là một trạng thái có ý nghĩa, mà trajectory hành vi, động lực và identity của customer có xu hướng hội tụ về phía đó theo thời gian.

Đây cũng là lý do paper gọi đây là **Attractor-Based Theory**.

---

# 5. Đây là lúc AI bước vào — 4:20–5:30

Framework sử dụng ba nhóm AI capability.

### Một: Deep Learning

Deep Learning giúp trả lời:

> **“Customer này đang ở trạng thái nào?”**

Từ hàng nghìn hoặc hàng triệu behavioral events:

Website.

Mobile App.

Transaction.

Search.

Content.

Campaign.

Customer Service.

Deep Learning có thể học ra một **latent Persona State**.

Nói đơn giản:

**Behavioral Events → Deep Learning → Current Persona**

---

### Hai: Persona Conversion Scoring

Sau đó chúng ta cần biết:

> **Customer này đã sẵn sàng hành động chưa?**

Đó là vai trò của **Persona Conversion Scoring**.

Ví dụ:

Product Fit: 90

Content Engagement: 80

Campaign Effectiveness: 40

Channel Performance: 75

Purchase Intent: 86.4

Tổng hợp lại có thể tạo ra:

> **PCS = 84**

Nhưng có một điểm rất quan trọng:

**84/100 không có nghĩa là 84% probability of conversion.**

Muốn diễn giải thành probability thì score phải được **calibrate và validate với historical outcomes**.

---

### Ba: Generative AI

Và đây là phần mình thấy thú vị nhất.

Nếu Deep Learning trả lời:

> **“Who is this customer now?”**

thì Generative AI trả lời:

> **“What should we create for this customer next?”**

Nó có thể tạo:

Personalized Content.

Recommendation.

Offer.

Explanation.

Conversation.

Learning Material.

Journey Intervention.

Service Experience.

Nhưng mục tiêu không phải đơn giản là tạo ra nhiều content hơn.

Mục tiêu là tạo ra **đúng intervention cho state hiện tại của customer**.

---

# 6. Từ Next Best Action đến Next Best Transformation Action — 5:30–6:30

Marketing hiện tại thường hỏi:

> **“What action will maximize conversion?”**

Framework này đề xuất một câu hỏi khác:

> **“What action can responsibly move the customer toward the desired state?”**

Đó là:

**Next Best Transformation Action — NBTA.**

Paper đưa ra nhiều ví dụ ngắn gọn:

Financially anxious → Financially confident: budget coaching, micro-saving plan.

Product curious → Informed buyer: so sánh minh bạch, giải thích trade-off.

Occasional user → Habitual user: personalized routine, progress feedback.

Uncertain customer → Confident decision maker: AI consultation minh bạch.

Và một ví dụ cụ thể hơn:

Customer đang:

**Sedentary**

Desired Persona:

**Active**

Next Best Action có thể là:

Không phải ngay lập tức:

> “Mua membership ngay!”

Mà có thể là:

> Beginner workout plan.

> First training session.

> Coaching.

> Progress tracking.

> Community introduction.

Tức là sản phẩm vẫn tồn tại.

Nhưng sản phẩm không còn nhất thiết là **destination**.

Nó trở thành một **instrument within the transformation journey**.

Paper gọi đây là sự chuyển đổi từ:

**Need → Product → Purchase**

sang:

**Aspiration → Transformation → Experience → Product**.

---

# 7. Closed Loop — 6:30–7:20

Và đây là diagram quan trọng nhất của toàn bộ framework:

**Current Persona**

↓

**Desired Persona**

↓

**Transformation Gap**

↓

**Next Best Transformation Action**

↓

**Personalized Experience**

↓

**Observed Outcome**

↓

**New Persona**

Và sau đó:

**New Persona**

lại trở thành input cho vòng tiếp theo.

Đây chính là **closed-loop personalization**.

Observe.

Infer.

Act.

Experience.

Observe Again.

Nói cách khác:

> **Customer không chỉ là đối tượng mà hệ thống personalize.**

Customer còn là người liên tục **dạy cho hệ thống cách personalize tốt hơn** thông qua response của họ.

Ngay cả rejection hay non-response cũng trở thành behavioral evidence mới.

---

# 8. Customer 360 nằm ở đâu? — 7:20–8:10

Vậy Customer 360 nằm ở đâu trong framework này?

Theo mình, Customer 360 chính là **foundation**.

Data Sources

↓

Identity Resolution

↓

Customer 360

↓

Persona / Segment

↓

Customer Journey

↓

Campaign & Activation

↓

Business Outcome

Và Business Outcome lại quay ngược trở lại Data.

Đây là điểm rất quan trọng.

Customer 360 không nên chỉ là:

> **“Một database chứa tất cả thông tin về customer.”**

Nó phải trở thành:

> **“Continuously updated representation of customer state.”**

Khi đó CDP bắt đầu tiến hóa thành **Customer Intelligence Platform**.

---

# 9. Từ Conversion Marketing đến Transformation Marketing — 8:10–9:00

Và đây có lẽ là thông điệp lớn nhất của paper.

Traditional Marketing:

**Customer as Target**

**Static Segment**

**Campaign**

**Funnel**

**Product**

**Conversion**

**Personalization**

**Recommendation**

**Customer Value**

**Optimization**

Nhưng Transformation-oriented Marketing:

**Customer as evolving person**

**Dynamic Persona**

**Intervention**

**Trajectory**

**Transformation Instrument**

**Behavioral Milestone**

**State-aware Adaptation**

**Next Best Transformation**

**Customer + Business + Social Value**

**Continuous Learning**

Tức là:

> **Targeting → Personalization → Transformation**

Đây là cách paper diễn giải sự chuyển dịch hướng tới Marketing 8.0. Và cũng cần nói rõ: **Marketing 8.0 trong paper là một proposed future-oriented framework của tác giả, không phải một taxonomy lịch sử chính thức.**

---

# 10. Nhưng có một ranh giới rất quan trọng — 9:00–9:50

Có một câu hỏi rất đáng sợ:

Nếu chúng ta có thể hiểu Persona, predict behavior và tạo ra personalized intervention...

**Liệu chúng ta đang giúp customer hay đang manipulate customer?**

Đây là lý do framework không đặt:

> **Maximize Conversion**

làm mục tiêu duy nhất.

Thay vào đó:

**Customer Value**

+

**Business Value**

+

**Social Value**

Và framework đề xuất bốn nguyên tắc:

**Customer Agency**

Customer có quyền accept hoặc reject.

**Transparency**

Không cố tình đánh lừa customer.

**Data Minimization**

Chỉ sử dụng những dữ liệu cần thiết và được phép.

**Non-manipulation**

Không khai thác vulnerability chỉ để tăng conversion.

Paper còn nhấn mạnh một điểm quan trọng: **Desired Persona của khách hàng** và **mục tiêu thương mại của công ty** không tự động giống nhau.

Hai mục tiêu này có thể overlap, nhưng hệ thống phải luôn phân biệt rõ ràng, và tối ưu theo Customer Value + Business Value + Social Value — chứ không chỉ tối ưu Conversion.

Đây không phải là một chi tiết phụ.

Nếu không có lớp này, **Customer Intelligence có thể trở thành Manipulation Intelligence.**

---

# 11. Vậy chúng ta đo thành công như thế nào? — 9:50–10:30

Nếu chỉ đo:

CTR.

Conversion.

Revenue.

thì chúng ta vẫn đang ở trong logic marketing cũ.

Framework đề xuất thêm những metrics như:

**Persona Alignment Score — PAS**

Customer đang gần Desired Persona đến đâu?

**Transformation Gap — TG**

Customer còn cách Desired Persona bao xa?

**Transformation Velocity — TV**

Customer đang tiến về phía Desired Persona nhanh đến mức nào?

**Conversion Propensity — CP**

Customer có khả năng thực hiện desired action đến đâu?

**Persona Drift — PD**

Persona đang thay đổi như thế nào?

Và cuối cùng:

**Transformation Value — TVa**

bao gồm:

Customer Value

+

Business Value

+

Social Value.

Mục tiêu là không giảm toàn bộ customer value thành short-term revenue.

---

# 12. Kết luận — 10:30–11:20

Nếu phải tóm tắt toàn bộ paper trong một câu, mình sẽ nói:

> **Customer không chỉ là người mà chúng ta muốn bán hàng cho. Customer là một con người đang thay đổi.**

Và nhiệm vụ của một **Customer Intelligence Platform** không chỉ là dự đoán:

> “Customer sẽ mua gì?”

mà phải tiến tới:

> **“Customer đang là ai?”**

> **“Customer muốn trở thành ai?”**

> **“Khoảng cách giữa hai trạng thái là gì?”**

> **“Chúng ta có thể tạo ra experience nào để hỗ trợ bước tiếp theo?”**

Và sau đó:

> **“Customer đã thay đổi như thế nào?”**

Để hệ thống tiếp tục học.

Đó là:

**Observe**

→ **Infer**

→ **Act**

→ **Experience**

→ **Observe Again**

Và cuối cùng:

> **From Customer Data**

> **to Customer 360**

> **to Customer Intelligence**

> **to Customer Transformation.**

Đó là ý tưởng mình muốn chia sẻ qua **Persona as a Vector**.

Và câu hỏi lớn tiếp theo không còn là:

> **“AI có thể predict customer behavior tốt đến đâu?”**

mà là:

> **“AI có thể giúp chúng ta hiểu con người tốt hơn đến đâu — và chúng ta có thể sử dụng sự hiểu biết đó một cách có trách nhiệm như thế nào?”**

Và cũng cần nói rõ: đây là một mô hình lý thuyết được đề xuất, chưa phải một quy luật tâm lý học hay marketing đã được kiểm chứng thực nghiệm đầy đủ. Nó cần được tiếp tục nghiên cứu, đo lường và validate.

Cảm ơn mọi người đã theo dõi.

---

## END SCREEN

**PERSONA AS A VECTOR**

*From Customer 360 to Customer Transformation*

**Customer Data → Customer 360 → Persona → AI → Transformation → Value**

**Deep Learning × Persona Conversion Scoring × Generative AI**

**Know the customer. Understand the state. Support the transformation.**