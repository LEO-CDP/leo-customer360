# Kịch bản Video Lecture: Persona as a Vector và Marketing 8.0

**Phụ đề:** Từ Customer 360 đến Customer Transformation

**Nguồn kiến thức:** `docs/research-papers/persona_as_a_vector_marketing_8.0.md`

**Thời lượng:** 30 phút

**Đối tượng:** Sinh viên Marketing, Product, Data, AI và Business
**Tone:** Bắt đầu từ vấn đề thực tế, sau đó đi vào lý thuyết và cách triển khai

## Learning Outcomes

Sau video, sinh viên có thể:

1. Giải thích vì sao persona tĩnh không đủ cho personalization trong ecommerce.
2. Biểu diễn current persona và desired persona dưới dạng vector.
3. Phân biệt vai trò của Deep Learning, Persona Conversion Scoring và Generative AI.
4. Chọn Next Best Transformation Action cho một khách hàng ecommerce.
5. Đánh giá framework bằng các chỉ số transformation, business và ethical value.
6. Thiết kế cách lưu persona state, version và history trong PostgreSQL kết hợp pgvector.
7. Dùng RAG để tạo semantic segment từ customer journey map nhưng vẫn giữ membership deterministic.
8. Chọn chiến lược personalization phù hợp với từng CX stage.

## Production Notes

- Hiện tình huống ecommerce trước khi hiển thị diagram của framework.
- Tất cả số liệu trong case study đều là synthetic và chỉ có mục đích minh họa.
- Khi nói về PCS, luôn hiện dòng **score không phải probability** trên màn hình.
- Sau mỗi câu hỏi, dừng lại một nhịp để sinh viên tự trả lời.
- Phần đầu là lecture nền tảng 20 phút; phần hai là implementation lecture 10 phút.
- Timestamp là thời lượng mục tiêu. Có thể dao động nhẹ, nhưng tổng video nên gần 30 phút.

---

## 0:00-1:30 — Opening: Ba câu hỏi quan trọng

Xin chào mọi người.

Chúng ta bắt đầu bằng ba câu hỏi.

**Câu hỏi thứ nhất:** Nếu một khách hàng đã xem cùng một sản phẩm năm lần, thêm sản phẩm vào giỏ hàng, bắt đầu checkout rồi rời đi, ecommerce system nên làm gì tiếp theo?

Có nên hiển thị discount?

Có nên đề xuất một sản phẩm rẻ hơn?

Có nên gửi reminder?

Hay khách hàng đang cần một điều khác, chẳng hạn thông tin sản phẩm rõ hơn, sự yên tâm về giao hàng, hoặc hỗ trợ so sánh các lựa chọn?

**Câu hỏi thứ hai:** Đây chỉ là một khách hàng có khả năng mua hàng cao, hay là một người đang trong quá trình trở thành một người ra quyết định tự tin hơn?

**Câu hỏi thứ ba:** Nếu hệ thống thuyết phục được khách hàng mua hàng, điều đó có nhất thiết có nghĩa là chúng ta đã tạo ra một customer outcome tốt hay không?

Các câu hỏi này đưa chúng ta ra khỏi một mục tiêu marketing rất hẹp: **maximize the next conversion**.

Chúng ta đi đến một nhóm câu hỏi sâu hơn:

> Khách hàng này đang ở trạng thái nào?
>
> Khách hàng muốn tiến tới trạng thái nào?
>
> Trải nghiệm tiếp theo nào có thể hỗ trợ khách hàng đi theo hướng đó một cách có trách nhiệm?

Hôm nay, chúng ta dùng paper **Persona as a Vector** để khám phá các câu hỏi này và kết nối chúng với một Marketing 8.0 framework được đề xuất.

---

## 1:30-4:00 — Vấn đề ecommerce: Khách hàng có intent cao nhưng vẫn không mua

Hãy bắt đầu bằng một tình huống cụ thể.

Một online retailer đang bán running shoes. Trong bảy ngày vừa qua, khách hàng Linh đã:

- đọc ba bài viết về cách chọn running shoes;
- xem bốn model nhiều lần;
- so sánh hai sản phẩm;
- đọc review về comfort và durability;
- thêm một đôi giày vào cart;
- bắt đầu checkout hai lần;
- rời đi trước bước payment; và
- mở một product email nhưng bỏ qua email discount.

Dashboard marketing có thể gắn nhãn Linh là **high intent**. Campaign manager có thể đề nghị gửi discount cuối cùng. Recommendation engine có thể đề xuất thêm giày. Retargeting system có thể tăng số lượng reminder.

Những hành động đó đều có thể xảy ra, nhưng không hành động nào chắc chắn là đúng.

Linh có thể đã đủ quan tâm đến sản phẩm. Vấn đề chưa được giải quyết có thể là confidence:

- Đôi giày nào phù hợp với người mới bắt đầu?
- Mức giá cao hơn có đáng hay không?
- Nếu sai size thì chuyện gì xảy ra?
- Đôi giày có thoải mái với routine thực tế của khách hàng không?

Vì vậy, cùng một event stream có thể dẫn đến những cách diễn giải khác nhau.

Cách diễn giải thứ nhất là:

> “Đây là khách hàng cần một incentive mua hàng mạnh hơn.”

Cách diễn giải thứ hai là:

> “Đây là khách hàng muốn trở thành một người mua hàng hiểu biết và tự tin hơn.”

Intervention sẽ thay đổi tùy theo cách diễn giải.

Với cách thứ nhất, hệ thống có thể gửi discount.

Với cách thứ hai, hệ thống có thể cung cấp comparison minh bạch, giải thích trade-off, tóm tắt các review liên quan, làm rõ return policy, hoặc đề nghị một cuộc tư vấn ngắn.

Đây là vấn đề thực tế mà framework muốn giải quyết:

> **Làm thế nào để hệ thống đi từ việc nhận biết một signal sang hiểu trạng thái đang thay đổi của khách hàng và chọn một trải nghiệm hữu ích tiếp theo?**

Các ecommerce use case không chỉ có abandoned checkout:

- giúp first-time shopper trở thành một confident consumer;
- giúp convenience-driven shopper khám phá các sản phẩm phù hợp hơn với mục tiêu sustainability;
- giúp occasional customer xây dựng routine mà không tạo ra áp lực không mong muốn; và
- giúp khách hàng chọn sản phẩm phù hợp, thay vì chỉ chọn sản phẩm có lợi nhuận cao nhất.

Mục tiêu không phải loại bỏ business outcome. Mục tiêu là hiểu business outcome như một phần của customer journey lớn hơn.

---

## 4:00-5:30 — “Marketing 8.0” có nghĩa gì trong bài giảng này?

Trước khi đi xa hơn, chúng ta cần nói chính xác về thuật ngữ **Marketing 8.0**.

Trong paper này, Marketing 8.0 là một **proposed future-oriented framework của tác giả**. Nó không được trình bày như một taxonomy lịch sử chính thức hay một marketing law đã được xác lập.

Paper mô tả một quá trình chuyển dịch mang tính khái niệm:

**Customer as Target**

tới

**Customer as Profile**

tới

**Customer as Dynamic Persona**

và sau đó hướng tới:

**Customer as a person moving through transformation**.

Traditional segmentation vẫn hữu ích. Demographic segment, lifecycle stage hoặc value tier giúp tổ chức cấu trúc quyết định. Nhưng một label như **Premium Customer** hoặc **Age 35 to 44** không cho chúng ta biết đủ về trạng thái của một người tại một thời điểm cụ thể.

Needs thay đổi.

Intent thay đổi.

Context thay đổi.

Confidence thay đổi.

Aspiration thay đổi.

Vì vậy, paper đưa ra một sự thay đổi trong unit of analysis:

> **Persona không chỉ là một label. Persona là một state đang chuyển đổi.**

Marketing 8.0, theo cách hiểu này, kết hợp human understanding, AI, personalization, transformation và purpose.

Điểm thay đổi không chỉ là dùng model lớn hơn hoặc tạo ra nhiều message hơn. Đó là thay đổi câu hỏi từ:

> “Chúng ta nên bán gì cho khách hàng này?”

sang:

> “Khách hàng đang ở trạng thái nào, họ coi trọng trạng thái nào, và chúng ta có thể cung cấp trải nghiệm nào để hỗ trợ bước tiếp theo một cách có trách nhiệm?”

---

## 5:30-8:00 — Persona as a Vector

Bây giờ chúng ta formalize ý tưởng này.

Thay vì gán cho Linh một persona cố định, chúng ta biểu diễn current persona dưới dạng một multidimensional state vector:

$$
\mathbf{P}(t) =
\begin{bmatrix}
V(t) & B(t) & N(t) & I(t) & E(t) & A(t) & R(t)
\end{bmatrix}
$$
Các dimension chỉ mang tính minh họa:

- $V(t)$: values và priorities;
- $B(t)$: behavioral patterns;
- $N(t)$: current needs và desired outcomes;
- $I(t)$: goal-directed intent;
- $E(t)$: emotional hoặc confidence state;
- $A(t)$: aspirations; và
- $R(t)$: relational và social influence.

Với Linh, một state ước lượng có thể như sau:

| Dimension | Signal hiện tại | Diễn giải |
|:--|:--|:--|
| Values | trung bình | Quan tâm đến comfort và value |
| Behavior | exploration cao | Nhiều comparison và review |
| Need | cao | Cần một đôi running shoes phù hợp |
| Intent | cao nhưng chưa hoàn tất | Đã thử checkout nhiều lần |
| Confidence | trung bình-thấp | Vẫn chưa chắc chắn về lựa chọn |
| Aspiration | cao | Muốn trở nên active hơn |
| Social influence | trung bình | Review ảnh hưởng đến quyết định |

Vector này không phải là phép đo toàn bộ personality của Linh. Nó là một representation ước lượng của state có liên quan đến customer experience hiện tại.

Điểm này rất quan trọng. Hệ thống quan sát các trace:

**Clicks, searches, purchases, content engagement, reviews, service conversations và declared preferences.**

Sau đó, hệ thống dùng inference model để ước lượng các latent variable như motivation, confidence hoặc intent.

Pipeline là:

$$
\text{Observable Signals}
\rightarrow
\text{Inference Model}
\rightarrow
\text{Estimated Persona State}
$$

Estimate này cần đi kèm uncertainty. “Purchase intent bằng 0.82” nên được hiểu là model ước lượng intent cao dựa trên evidence hiện có. Nó không nên được xem là một sự thật tuyệt đối về khách hàng.

Context cũng rất quan trọng. Cùng một khách hàng có thể phản ứng khác nhau ở các thời điểm khác nhau vì device, budget, life situation, campaign exposure hoặc recent experience đã thay đổi. Ta có thể biểu diễn điều đó như sau:

$$
\mathbf{P}(t+\Delta t)=F\left(\mathbf{P}(t),\mathbf{C}(t),\mathbf{S}(t)\right)
$$

Trong đó, $\mathbf{C}(t)$ là context và $\mathbf{S}(t)$ là external stimulus hoặc observed event. Stimulus không tự quyết định behavior. Tác động của nó phụ thuộc vào current state và context.

---

## 8:00-10:00 — Current Persona, Desired Persona và Transformation Gap

Current state trả lời câu hỏi:

> “Khách hàng đang ở đâu?”

**Desired Persona** trả lời câu hỏi:

> “Khách hàng muốn tiến tới state có ý nghĩa nào?”

Với Linh, desired state có thể là:

> **Confident and informed consumer đang bắt đầu một running routine bền vững.**

Chúng ta biểu diễn current và desired state như sau:

$$
\mathbf{P}_c(t)=\text{Current Persona}
$$

$$
\mathbf{P}_d=\text{Desired Persona}
$$

Khoảng cách giữa hai state là **Transformation Gap**:

$$
TG(t)=D\left(\mathbf{P}_c(t),\mathbf{P}_d\right)
$$

Distance function $D$ có thể là Euclidean distance, cosine distance, learned metric hoặc domain-specific function. Paper không nói rằng một metric duy nhất luôn đúng. Việc chọn và validate metric cũng là một research problem.

Desired persona được mô tả như một conceptual **attractor**. Đây không phải là một psychological force hay physical force theo nghĩa đen. Nó là một state có ý nghĩa mà behavior, motivation, identity và experience có thể tiến gần tới.

Với Linh, trajectory có thể là:

$$
\text{Curious}
\rightarrow
\text{Comparing}
\rightarrow
\text{Informed}
\rightarrow
\text{First Purchase}
\rightarrow
\text{Beginning a Routine}
$$

Purchase quan trọng, nhưng chỉ là một milestone trong trajectory. Nó không phải toàn bộ ý nghĩa của journey.

Từ đây, chúng ta có một câu hỏi personalization hữu ích hơn:

> **Trải nghiệm nào có thể làm giảm transformation gap mà không tước đi agency của khách hàng?**

---

## 10:00-12:30 — Ba AI capabilities trong framework

Paper kết nối ba AI capability với ba nhiệm vụ khác nhau.

### 1. Deep Learning: Ước lượng current state

Deep Learning xử lý behavioral history:

**Website events, mobile activity, search, product interaction, content, campaigns, transactions và service conversations.**

Về mặt khái niệm:

$$
\hat{\mathbf{P}}(t)=f_\theta(X_{1:t})
$$

Model ước lượng latent persona state từ event history. Đây là **persona perception**: ước lượng khách hàng hiện đang có vẻ như thế nào.

Model cũng cần thể hiện confidence và uncertainty. Một estimate có confidence cao và một estimate yếu không nên kích hoạt cùng một intervention.

### 2. Persona Conversion Scoring: Ước lượng readiness cho một action

**Persona Conversion Score**, hay PCS, kết hợp các signal liên quan đến readiness cho một desired action:

$$
PCS=\sum_{i=1}^{n}w_iD_i
$$

Ví dụ, một ecommerce score mang tính minh họa:

$$
PCS=0.30P+0.25C+0.15K+0.08Ch+0.22I
$$

Các dimension lần lượt đại diện cho Product Fit, Content Engagement, Campaign Effectiveness, Channel Performance và Purchase Intent.

Giả sử score của Linh là 81.2 trên 100.

Đó là một tập hợp conversion signal mạnh. Nhưng hãy nhớ nguyên tắc:

> **PCS score không tự động là conversion probability.**

Score 81.2 không có nghĩa là khách hàng có 81.2 phần trăm khả năng conversion. Muốn diễn giải nó như probability, model phải được kiểm tra với historical outcome và calibrated cho một time window xác định.

### 3. Generative AI: Tạo ra trải nghiệm tiếp theo

Nếu Deep Learning hỏi:

> “Khách hàng hiện đang ở trạng thái nào?”

và PCS hỏi:

> “Khách hàng readiness cho action này đến đâu?”

thì Generative AI hỏi:

> “Tiếp theo chúng ta nên tạo ra trải nghiệm gì cho khách hàng?”

Nó có thể tạo explanation, comparison, recommendation, offer, conversation, learning material và service experience.

Tuy nhiên, generation cần được condition theo current state, desired state, context, consent và business constraints. Mục tiêu không phải là nhiều content hơn. Mục tiêu là đúng experience cho transformation gap hiện tại.

---

## 12:30-15:00 — Ecommerce use case: Chọn Next Best Transformation Action

Bây giờ hãy áp dụng framework vào trường hợp của Linh.

Câu hỏi truyền thống là:

> “Action nào sẽ maximize conversion?”

Câu hỏi được đề xuất là:

> “Action nào có thể giúp khách hàng tiến gần desired state một cách hiệu quả và có trách nhiệm?”

Đó là **Next Best Transformation Action**, hay NBTA.

Evidence hiện tại cho chúng ta biết:

- product interest cao;
- comparison behavior cao;
- content engagement cao;
- checkout intent cao; và
- confidence vẫn chưa hoàn chỉnh.

Các action có thể gồm:

1. Gửi discount ngay lập tức.
2. Đề xuất một đôi giày rẻ hơn.
3. Cung cấp comparison side-by-side tập trung vào comfort, durability và fit.
4. Tóm tắt review theo đúng các concern Linh đã thể hiện.
5. Giải thích rõ size và return policy.
6. Cung cấp beginner running guide và tạm giảm product pressure.

Action nào tốt nhất phụ thuộc vào customer goal và evidence. Nếu transformation gap là confidence, transparent comparison có thể tốt hơn discount. Nếu affordability là barrier thật sự, sản phẩm giá thấp hơn có thể phù hợp. Nếu khách hàng chưa hình thành running routine, beginner guide có thể tạo value nhiều hơn một product reminder khác.

Product vẫn quan trọng. Điểm khác biệt là product trở thành một instrument bên trong experience:

$$
\text{Product}
+
\text{Explanation}
+
\text{Fit Support}
+
\text{Progress Feedback}
\rightarrow
\text{More Confident Customer}
$$

Các ecommerce use case khác:

| Current state | Desired state | NBTA có thể có |
|:--|:--|:--|
| Product curious | Informed buyer | Transparent comparison và trade-off explanation |
| First-time shopper | Confident customer | Guided discovery và return information rõ ràng |
| Occasional user | Habitual user | Routine hữu ích, reminder có customer control và progress feedback |
| Convenience-driven | More deliberate consumer | Product education và sustainable alternative phù hợp |
| Uncertain at checkout | Decision-ready customer | Suitability explanation, không chỉ pressure |

Framework không nói rằng mọi khách hàng phải được biến thành một commercial identity mà doanh nghiệp mong muốn. Desired state phải có ý nghĩa với khách hàng, được họ thể hiện hoặc được suy luận một cách có trách nhiệm, và có thể được điều chỉnh.

---

## 15:00-16:30 — Closed Loop

Framework không phải là một segmentation exercise chỉ thực hiện một lần. Nó là một feedback loop:

$$
\begin{aligned}
\text{Current Persona}
&\rightarrow \text{Desired Persona}
\rightarrow \text{Transformation Gap}
\\
&\rightarrow \text{Next Best Transformation Action}
\rightarrow \text{Personalized Experience}
\\
&\rightarrow \text{Observed Outcome}
\rightarrow \text{New Persona}
\end{aligned}
$$

Giả sử Linh bỏ qua comparison nhưng đọc return policy và truy cập nhóm sản phẩm affordable. Hệ thống nên cập nhật cách diễn giải. Barrier có thể là price hoặc risk, không phải thiếu information.

Giả sử Linh mua hàng nhưng không bao giờ tương tác với running content nữa. Hệ thống không nên mặc định rằng purchase đã tạo ra một long-term running identity.

Giả sử Linh từ chối mọi product recommendation nhưng bắt đầu đọc beginner training content. Hệ thống nên cân nhắc rằng desired state thực sự là một active lifestyle, còn product decision chỉ là một bước.

Rejection, non-response và unexpected behavior là evidence mới. Chúng không phải lý do để hệ thống cứ tăng pressure.

Trong một decision system production, paper kết nối loop này với nhiều algorithm family:

- sequence models hoặc filtering cho persona-state estimation;
- contextual bandits hoặc offline reinforcement learning cho action selection;
- uplift modeling và causal evaluation cho intervention effect; và
- Generative AI cho content hoặc experience cuối cùng.

Nguyên tắc quan trọng là correlation không phải causation. Khách hàng có thể đã mua dù không nhận intervention. Vì vậy, transformation measurement cần holdout, experiment hoặc phương pháp causal phù hợp.

---

## 16:30-17:45 — Customer 360 nằm ở đâu?

Customer 360 là foundation, không phải intelligence layer cuối cùng.

Operational flow là:

$$
\text{Data Sources}
\rightarrow
\text{Identity Resolution}
\rightarrow
\text{Customer 360}
\rightarrow
\text{Persona State}
\rightarrow
\text{Journey}
\rightarrow
\text{Activation}
\rightarrow
\text{Outcome}
$$

Trong ecommerce, data source có thể gồm web và mobile events, catalog interaction, transactions, service conversations, campaign exposure và customer-provided preferences.

Identity resolution giúp hợp nhất các signal đó. Customer 360 cung cấp longitudinal representation. Persona modeling ước lượng current state. Journey và activation system chọn rồi phân phối experience. Outcome quay trở lại để cập nhật state tiếp theo.

Vì vậy, Customer 360 không nên được hiểu là một static database chứa tất cả thông tin về một người. Nó nên được hiểu là một continuously updated representation của relevant customer state, đi cùng permissions, uncertainty và historical context.

Đây là chuyển dịch từ:

**Customer Data**

tới

**Customer 360**

tới

**Customer Intelligence**

tới

**Customer Transformation**.

---

## 17:45-19:00 — Đo lường và sử dụng có trách nhiệm

Conversion và revenue vẫn quan trọng. Nhưng chúng chưa đủ.

Paper đề xuất các chỉ số bổ sung:

- **Persona Alignment Score:** current state gần desired state đến đâu;
- **Transformation Gap:** còn bao nhiêu khoảng cách;
- **Transformation Velocity:** khách hàng tiến về desired state nhanh đến đâu;
- **Conversion Propensity:** calibrated probability của desired action;
- **Persona Drift:** inferred state thay đổi bao nhiêu theo thời gian; và
- **Transformation Value:** customer value cộng business value cộng social value.

Các metric cần được đọc cùng nhau. Một system có thể tăng conversion nhưng làm giảm trust. Nó có thể tăng engagement nhưng tạo ra confusion hoặc dependency. Nó có thể cải thiện short-term revenue nhưng đưa khách hàng xa hơn mục tiêu của chính họ.

Từ đó xuất hiện ethical boundary.

Customer's desired persona và company's commercial objective không tự động giống nhau:

$$
\text{Customer Goal} \neq \text{Company Goal}
$$

Framework cần ít nhất bốn nguyên tắc:

1. **Customer agency:** khách hàng có thể accept, reject hoặc revise recommendation.
2. **Transparency:** experience không cố tình che giấu trade-off quan trọng.
3. **Data minimization:** system chỉ dùng signal phù hợp và được cho phép.
4. **Non-manipulation:** system không khai thác vulnerability chỉ để tăng conversion.

Nếu thiếu các constraint này, Customer Intelligence có thể trở thành Manipulation Intelligence.

Paper là một proposed theoretical model. Persona vector là một simplification của human identity. Attractor là conceptual analogy, không phải physical law. Các dimension, distance function, causal effect và business value đều cần được empirical validation.

---

## 19:00-20:00 — Kết luận và câu hỏi cho sinh viên

Hãy quay lại ba câu hỏi ở phần mở đầu.

Một ecommerce system nên làm gì khi khách hàng liên tục xem sản phẩm rồi bỏ checkout?

Câu trả lời không tự động là “gửi discount”. Trước hết, hãy ước lượng current state và xác định transformation gap có khả năng tồn tại.

Khách hàng chỉ đơn giản là người có khả năng mua cao, hay họ đang trở thành một decision maker tự tin hơn?

Framework yêu cầu chúng ta nhìn thấy cả commercial action và human trajectory.

Và một purchase thành công có nhất thiết là một customer outcome tốt hay không?

Không. Chúng ta cần đo customer value, business value, social value, trust và long-term movement, chứ không chỉ immediate transaction.

Ý tưởng trung tâm là:

> **Khách hàng không chỉ là target của conversion. Khách hàng là một con người có state thay đổi theo thời gian.**

Marketing 8.0 framework được đề xuất dùng AI để hỗ trợ cách hiểu đó:

**Observe** các signal.

**Infer** current state.

**Define** hoặc confirm desired state.

**Choose** next best transformation action.

**Create** relevant experience.

**Observe again** và học từ outcome.

Tóm tắt trong một dòng:

$$
\text{Current Persona}
\rightarrow
\text{Desired Persona}
\rightarrow
\text{Transformation}
\rightarrow
\text{Value}
$$

Một câu hỏi cuối cho discussion:

> **Trong một ecommerce journey mà bạn biết rõ, đâu là khác biệt giữa giúp khách hàng ra quyết định và thuyết phục khách hàng mua hàng?**

Đó là ranh giới nơi lý thuyết trở thành trách nhiệm về design và governance.

Cảm ơn mọi người.

---

# Phần II — Từ Framework đến Implementation

## 20:00-22:00 — PostgreSQL và pgvector cho Persona Modeling

Bây giờ chúng ta chuyển từ lý thuyết sang cách triển khai.

Một nguyên tắc thiết kế quan trọng là:

> **PostgreSQL giữ state có cấu trúc và lịch sử. pgvector giữ biểu diễn vector cho similarity và retrieval.**

Không nên nhét toàn bộ customer state vào một embedding rồi xem embedding đó là source of truth. Embedding khó giải thích, khó audit và không thay thế được các trường như tenant, customer, model version, confidence, lifecycle stage hay thời điểm tính toán.

Một persona implementation thực tế có thể tách thành bốn lớp dữ liệu:

1. **Persona archetype:** persona dùng chung, ví dụ `confident_consumer`, kèm centroid embedding.
2. **Current persona assignment:** customer hiện được gán vào archetype nào, với score, confidence, active flag và version.
3. **Features và score details:** những signal nào tạo ra persona và cách mỗi score được tính.
4. **Persona history:** các thay đổi quan trọng theo thời gian để audit trajectory và giải thích drift.

Trong Customer 360 repository, pattern này tương ứng với các nhóm `cdp_persona_archetypes`, `cdp_customer_personas`, `cdp_persona_features`, `cdp_persona_score_details` và `cdp_persona_history`. Archetype có `persona_embedding` 768 chiều; assignment có `computed_version`, `is_active`, scores, lifecycle stage và next-best action.

Nếu thiết kế một state table mới cho bài học, ta có thể hình dung schema tối thiểu như sau:

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE cdp_persona_states (
	persona_state_id  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
	tenant_id         uuid NOT NULL,
	master_profile_id uuid NOT NULL,
	state_version     integer NOT NULL,
	journey_stage     text NOT NULL,
	persona_label     text,
	dimensions        jsonb NOT NULL,
	embedding         vector(768),
	confidence        numeric(5, 4),
	model_version     text NOT NULL,
	is_active         boolean NOT NULL DEFAULT true,
	computed_at       timestamptz NOT NULL DEFAULT now(),
	UNIQUE (tenant_id, master_profile_id, state_version)
);

CREATE INDEX cdp_persona_states_embedding_hnsw
ON cdp_persona_states USING hnsw (embedding vector_cosine_ops);
```

`dimensions` có thể chứa các giá trị như intent, confidence, aspiration và behavioral scores dưới dạng JSONB để dễ mở rộng. Nhưng những thuộc tính cần filter thường xuyên, chẳng hạn `tenant_id`, `journey_stage`, `is_active` và `model_version`, nên là cột riêng để query và kiểm soát quyền rõ ràng.

`vector(768)` chỉ là ví dụ phải khớp với embedding model. RAG documentation service trong repository hiện dùng model 384 chiều và bảng `rag.doc_chunks` có `vector(384)`. Persona embedding và document embedding có thể dùng dimension khác nhau vì chúng phục vụ hai không gian semantic khác nhau. Không được trộn hai loại vector chỉ vì cùng dùng pgvector.

---

## 22:00-24:00 — State Persistence: Version, History và Similarity Query

Persona state không nên bị overwrite một cách im lặng.

Mỗi lần model tính lại state, transaction nên thực hiện các bước sau:

1. Xác định đúng tenant và customer.
2. Đóng hoặc đánh dấu bản ghi active cũ là inactive.
3. Insert một state version mới.
4. Lưu feature inputs, score breakdown, model version và confidence.
5. Ghi history nếu label, score hoặc journey stage thay đổi đáng kể.
6. Commit cùng transaction để current state và audit history không lệch nhau.

> **State hiện tại phục vụ activation; history phục vụ learning, explainability và rollback.**

Ví dụ, query để tìm các persona archetype gần với một profile vector có thể là:

```sql
SELECT
	persona_archetype_id,
	persona_code,
	persona_name,
	1 - (persona_embedding <=> %(profile_embedding)s) AS similarity
FROM cdp_persona_archetypes
WHERE tenant_id = %(tenant_id)s
  AND is_active = TRUE
  AND persona_embedding IS NOT NULL
ORDER BY persona_embedding <=> %(profile_embedding)s
LIMIT 10;
```

Toán tử `<=>` là cosine distance trong pgvector; `1 - distance` được dùng ở đây để hiển thị similarity. Query phải truyền vector qua parameter binding, không nối chuỗi từ input người dùng.

Tenant filter không phải là một điều kiện tùy chọn. Mọi bảng customer-owned cần có `tenant_id`, foreign key phù hợp và index/query path tương ứng. Ở tầng segmentation, transaction còn phải set tenant context trước khi chạy SQL để Row-Level Security có thể chặn truy cập chéo tenant.

Ta cũng cần phân biệt ba loại query:

- **Relational filter:** “khách hàng active trong tenant này, lifecycle stage là consideration”.
- **Vector similarity:** “archetype nào gần state vector này nhất?”.
- **History query:** “state đã thay đổi như thế nào trong 30 ngày qua?”.

Không loại nào thay thế được hai loại còn lại. Một hệ thống tốt kết hợp cả ba để trả lời “khách hàng đang ở đâu, vì sao hệ thống nghĩ như vậy, và nên làm gì tiếp theo”.

---

## 24:00-26:00 — RAG cho Semantic Segmentation từ Customer Journey Map

Bây giờ hãy thêm RAG.

RAG không nên được dùng để cho LLM tự quyết định customer membership bằng một câu trả lời tự do. RAG phù hợp hơn với việc hiểu ngữ nghĩa của journey, tìm các pattern tương tự và tạo ra một segment proposal có evidence.

Ta có thể chuyển journey của Linh thành một journey document theo stage:

```text
tenant: t01
profile: p123
stage: consideration
window: last_30_days
events: product_view, compare, review_read, checkout_start, checkout_abandon
signals: high_product_interest, high_content_engagement, medium_low_confidence
goal: become a confident and informed buyer
barrier: fit and return-risk uncertainty
```

Document này được chunk, embed và lưu vào bảng kiểu `journey_chunks`:

```sql
CREATE TABLE journey_chunks (
	chunk_id       text PRIMARY KEY,
	tenant_id      uuid NOT NULL,
	master_profile_id uuid NOT NULL,
	journey_map_id text NOT NULL,
	cx_stage       text NOT NULL,
	content        text NOT NULL,
	metadata       jsonb NOT NULL DEFAULT '{}'::jsonb,
	embedding      vector(384) NOT NULL,
	content_hash   text NOT NULL,
	observed_at    timestamptz NOT NULL
);

CREATE INDEX journey_chunks_embedding_hnsw
ON journey_chunks USING hnsw (embedding vector_cosine_ops);
```

Khi marketer hỏi:

> “Tìm những khách hàng ở consideration stage có product interest cao nhưng chưa đủ confidence để mua running shoes.”

pipeline có thể là:

1. Embed câu hỏi.
2. Lọc theo `tenant_id`, consent và time window.
3. Retrieve top-N bằng cosine similarity trong pgvector.
4. Rerank các journey chunks liên quan.
5. RAG tạo segment proposal, lý do và evidence source.
6. Chuyển proposal thành điều kiện có thể kiểm tra được, ví dụ event counts, score threshold và `cx_stage`.
7. Chạy deterministic SQL để tính membership trong `cdp_segments`.
8. Gắn tag hoặc audience snapshot và ghi lại query, model version, thời điểm recompute.

Đây là điểm phân chia trách nhiệm:

> **RAG tìm và giải thích meaning; PostgreSQL quyết định membership có thể audit.**

Repository hiện có một RAG flow tương tự: embed query, tìm top-N chunk trong pgvector, rerank, rồi tạo grounded answer cùng source paths. Document chunks dùng cosine search và HNSW index. Ta có thể tái sử dụng pattern đó cho journey semantics, nhưng phải thêm tenant scope, consent, profile authorization và không đưa PII không cần thiết vào prompt.

Ví dụ, RAG có thể đề xuất segment:

**`consideration_fit_uncertainty`** — khách hàng đã so sánh ít nhất hai sản phẩm, có content engagement cao, có checkout attempt, nhưng chưa purchase và đang tương tác với fit hoặc return content.

Nhưng segment chính thức chỉ được tạo sau khi rule tree hoặc SQL rule được validate. Nếu RAG không tìm đủ evidence, hệ thống phải trả về “insufficient evidence” thay vì bịa ra một persona.

---

## 26:00-28:00 — Personalization Strategy theo từng CX Stage

Persona không nên được dùng giống nhau ở mọi stage. Cùng một customer state có thể cần một trải nghiệm khác tùy vị trí trong journey.

| CX stage | Persona question | Personalization strategy | Ecommerce example | KPI và guardrail |
|:--|:--|:--|:--|:--|
| **Awareness / Discovery** | Khách hàng đang quan tâm điều gì? | Giáo dục và discovery, chưa tạo pressure mua hàng | Nội dung “cách chọn running shoes” theo aspiration và knowledge level | Content quality, engaged time; tránh retargeting quá dày |
| **Consideration** | Barrier là price, fit, trust hay thiếu thông tin? | So sánh, review summary, explanation và social proof phù hợp | So sánh comfort/durability; giải thích trade-off thay vì discount mặc định | Comparison completion, confidence signal; không che giấu trade-off |
| **Conversion / Checkout** | Khách hàng cần giảm friction nào? | Hỗ trợ quyết định, return/size clarity, channel continuity | Size assistant, delivery estimate, saved cart và alternative phù hợp | Checkout completion, return rate; không dùng dark pattern |
| **Onboarding / First Use** | Khách hàng có đạt outcome đầu tiên không? | Hướng dẫn, setup, first-success intervention và support | Hướng dẫn bắt đầu chạy, chăm sóc giày và plan tuần đầu | First-use completion, support satisfaction; tôn trọng consent |
| **Retention / Growth** | Persona có đang hình thành routine không? | Progress feedback, replenishment hữu ích và next-best experience | Nhắc thay giày dựa trên usage, không chỉ calendar spam | Repeat value, retention, transformation velocity; customer control |
| **Advocacy / Win-back** | Khách hàng muốn chia sẻ hay đang drift/churn? | Community, feedback, service recovery hoặc re-entry nhẹ nhàng | Mời review sau outcome thật; win-back bằng lý do phù hợp | Trust, referral, reactivation; không khai thác frustration |

Có thể liên kết bảng CX này với data journey của Customer 360:

$$
Capture
\rightarrow
Assemble
\rightarrow
Score
\rightarrow
Segment
\rightarrow
Syndicate
\rightarrow
Engage
$$

CX stage là ngữ cảnh trải nghiệm. Data stage là cách platform vận hành. Chúng không phải cùng một khái niệm, nhưng phải nối với nhau. Ví dụ, `consideration` trong CX có thể cần dữ liệu từ `Capture`, `Assemble` và `Score`, rồi activation qua `Syndicate` và `Engage`.

---

## 28:00-30:00 — Kiến trúc hoàn chỉnh và bài tập cho sinh viên

Hãy ghép toàn bộ flow lại:

```text
Events + Consent
	-> Identity Resolution
	-> Customer 360
	-> Features + Persona State
	-> PostgreSQL + pgvector
	-> Journey Map Retrieval
	-> RAG Segment Proposal
	-> Deterministic Segment Membership
	-> CX-stage Personalization
	-> Observed Outcome + Persona History
```

Có bốn nguyên tắc cần nhớ:

1. **Lưu state có cấu trúc và history trong PostgreSQL; dùng vector cho similarity, không dùng vector thay cho sự thật nghiệp vụ.**
2. **Giữ embedding dimension, model version, content hash và index configuration nhất quán.**
3. **Dùng RAG để tìm meaning, context và evidence; dùng SQL để tạo membership, recompute và audit.**
4. **Personalize theo CX stage, customer goal và confidence; không chỉ theo conversion propensity.**

Bài tập cuối video:

Chọn một ecommerce journey, chẳng hạn thời trang, điện tử hoặc grocery. Sau đó:

- viết current persona và desired persona;
- chọn ba event signals và hai uncertainty signals;
- thiết kế một bảng persona state có version và history;
- viết một semantic segment question cho RAG;
- chuyển proposal đó thành một membership rule có thể chạy bằng SQL; và
- chọn một intervention cho awareness, consideration và checkout.

Khi review bài, hãy hỏi ba câu:

> Evidence nào đến từ customer behavior, evidence nào là model inference?
>
> Segment này có thể giải thích và tái lập từ database không?
>
> Intervention đang giúp customer đạt mục tiêu của họ, hay chỉ đang tối ưu conversion?

Đó là cách biến Persona as a Vector từ một ý tưởng lý thuyết thành một hệ thống có thể lưu trữ, truy hồi, giải thích và vận hành có trách nhiệm.

## End Screen

**PERSONA AS A VECTOR**

*From Customer 360 to Customer Transformation*

**Customer Data -> Customer 360 -> Persona State -> PGSQL + pgvector -> RAG -> CX Personalization -> Value**

**Deep Learning + Persona Conversion Scoring + Generative AI**

**Understand the state. Retrieve the journey. Support the next step. Preserve customer agency.**
