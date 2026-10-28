Với một Customer Profile trong LEO CDP, chứa dữ liệu giao dịch, hành vi xem sản phẩm và vị trí địa lý, bạn có thể xây dựng mô hình đo lường giá trị kinh tế của khách hàng, ý định mua hàng và giá trị gia tăng mà dữ liệu mang lại cho doanh nghiệp.

Điểm quan trọng: một hồ sơ có nhiều dữ liệu chưa chắc có giá trị cao. Giá trị thực sự nằm ở khả năng dự đoán hành vi và tạo ra lợi nhuận tăng thêm.

## 1. Mô hình định giá Customer Profile

Customer Profile

Identity · Demographics · Consent

Transaction

Chi tiêu, tần suất, biên lợi nhuận

Product View

Sở thích, ý định mua

Location

Khoảng cách, khả năng tiếp cận

Customer Lifetime Value

Khách hàng có thể tạo ra bao nhiêu lợi nhuận?

Purchase Propensity

Khách hàng có khả năng mua gì tiếp theo?

Incremental Business Value

Lợi nhuận tăng thêm nhờ quyết định marketing dựa trên dữ liệu

## 2. Ví dụ dữ liệu của một khách hàng

Giả sử một khách hàng của doanh nghiệp bán lẻ có hồ sơ như sau:

| Thuộc tính                        | Giá trị mẫu    |
| --------------------------------- | -------------- |
| Customer ID                       | C001           |
| Tổng giao dịch 12 tháng           | 6              |
| Doanh thu 12 tháng                | 18.000.000 VNĐ |
| Biên lợi nhuận gộp                | 30%            |
| Số lần xem sản phẩm trong 30 ngày | 20             |
| Số lần thêm vào giỏ               | 2              |
| Lần mua gần nhất                  | 10 ngày trước  |
| Khoảng cách tới cửa hàng          | 2 km           |

Từ dữ liệu này, ta có thể tính ba giá trị khác nhau.

### A. Giá trị kinh tế của khách hàng — Customer Lifetime Value (CLV)

Công thức CLV theo lợi nhuận đóng góp kỳ vọng:

\\[ CLV_H=\sum\_{t=1}^{H} \frac{S_t \times M_t}{(1+r)^t}-CAC \\]

Trong đó:

- \\(S_t\\): xác suất khách hàng còn hoạt động ở năm \\(t\\).
- \\(M_t\\): lợi nhuận đóng góp kỳ vọng trong năm đó nếu khách hàng hoạt động.
- \\(r\\): tỷ lệ chiết khấu.
- \\(CAC\\): chi phí thu hút khách hàng, nếu cần tính hiệu quả sau chi phí thu hút.

Giả sử khách hàng có tần suất mua kỳ vọng 4 đơn/năm, giá trị đơn hàng trung bình 3 triệu đồng, biên lợi nhuận gộp 30%, chi phí marketing và phục vụ là 800.000 đồng/năm.

Lợi nhuận đóng góp khi khách hàng còn hoạt động:

\\[ M=4\times3.000.000\times30\\%-800.000 \\]

\\[ M=\boxed{2.800.000\text{ VNĐ/năm}} \\]

Nếu tỷ lệ duy trì hằng năm là 75%, tỷ lệ chiết khấu 10%, thời gian dự báo 3 năm và CAC đã phát sinh là 500.000 đồng:

\\[ \begin{aligned} CLV_3={}&\frac{0,75\times2,8}{1,1}\\\ &+\frac{0,75^2\times2,8}{1,1^2}\\\ &+\frac{0,75^3\times2,8}{1,1^3}\\\ &-0,5 \end{aligned} \\]

Các giá trị tiền trong công thức trên tính bằng triệu đồng. CLV 3 năm sau CAC xấp xỉ 3,60 triệu đồng.

Đây là con số minh họa theo giả định, không phải dự báo thực nghiệm. Trong CDP thực tế, xác suất duy trì, số đơn hàng và giá trị đơn hàng nên được ước lượng từ dữ liệu lịch sử.

### B. Purchase Intent Score — điểm ý định mua

Bạn có thể bắt đầu với mô hình rule-based để kiểm thử trước khi huấn luyện machine learning.

\\[ IntentScore=V\_{score}+C\_{score}+R\_{score}+L\_{score} \\]

Trong đó:

- \\(V\\): mức độ xem sản phẩm.
- \\(C\\): hành vi thêm vào giỏ.
- \\(R\\): độ gần của lần mua gần nhất.
- \\(L\\): mức độ thuận tiện khi tiếp cận cửa hàng, chỉ áp dụng cho use case phù hợp.

Một bộ trọng số minh họa là 25 điểm cho product view, 35 điểm cho add-to-cart, 25 điểm cho recency và 15 điểm cho location. Với khách hàng trên, điểm theo quy tắc mẫu xấp xỉ 85,6/100.

Lưu ý: đây là điểm quy tắc do doanh nghiệp đặt ra, không phải xác suất mua hàng 85,6%. Muốn chuyển thành xác suất thực tế, bạn cần huấn luyện và hiệu chỉnh mô hình bằng các kết quả mua hàng quan sát được.

### C. Incremental Business Value — giá trị tăng thêm từ dữ liệu

Đây là chỉ số hữu ích nhất để đánh giá CDP có thực sự tạo ra lợi ích kinh doanh hay không.

\\[ \begin{aligned} IncrementalProfit={}&(P\_{campaign}-P\_{baseline})\\\ &\times AOV\times Margin\\\ &-CampaignCost \end{aligned} \\]

Giả sử mô hình dự đoán chiến dịch cá nhân hóa nâng xác suất mua hàng từ 4% lên 7%, AOV là 3 triệu đồng, biên lợi nhuận gộp 30%, chi phí tiếp cận là 2.000 đồng và chi phí ưu đãi kỳ vọng là 5.000 đồng trên mỗi khách hàng được tiếp cận.

\\[ \begin{aligned} IncrementalProfit &=(7\\%-4\\%)\times3.000.000\times30\\%\\\ &\quad-2.000-5.000\\\ &=\boxed{20.000\text{ VNĐ/khách hàng}} \end{aligned} \\]

Đây là lợi nhuận tăng thêm kỳ vọng trên mỗi khách hàng được tiếp cận, với điều kiện chênh lệch chuyển đổi là do chiến dịch tạo ra. Trong thực tế, nên xác minh điều này bằng A/B test hoặc holdout group.

## 3. Python: tính CLV, ý định mua và lợi nhuận chiến dịch

Đoạn code hoàn chỉnh, chỉ sử dụng thư viện chuẩn Python:

```
        annual_service_and_marketing_cost,        acquisition_cost,    ) < 0:        raise ValueError("Financial inputs cannot be negative.")    annual_contribution = (        expected_orders_per_year        * average_order_value        * gross_margin        - annual_service_and_marketing_cost    )    pv_contribution = 0.0    for year in range(1, years + 1):        survival_probability = annual_retention ** year        expected_profit = (            survival_probability * annual_contribution        )        pv_contribution += expected_profit / (            (1 + discount_rate) ** year        )    return {        "annual_contribution": annual_contribution,        "clv_before_cac": pv_contribution,        "cac": acquisition_cost,        "clv_after_cac": pv_contribution - acquisition_cost,    }def purchase_intent_score(    profile: CustomerProfile,    use_location: bool = True,) -> float:    """    Illustrative rule-based score, not a calibrated probability.    Location contributes only for a relevant store-based use case.    """    views_score = (        min(profile.product_views_30d / 20, 1.0) * 25    )    cart_score = (        min(profile.add_to_cart_30d / 3, 1.0) * 35    )    recency_score = max(        0.0,        25 * (            1 - profile.days_since_last_purchase / 90        ),    )    if use_location:        if profile.distance_to_store_km <= 3:            location_score = 15        elif profile.distance_to_store_km <= 10:            location_score = 8        else:            location_score = 0    else:        location_score = 0    maximum_score = 100 if use_location else 85    raw_score = (        views_score        + cart_score
```

## 4. Kiến trúc chỉ số đề xuất cho LEO CDP

Không nên gộp tất cả thành một điểm số duy nhất ngay từ đầu. Nên lưu các chỉ số có ý nghĩa riêng biệt trong Customer 360:

| Chỉ số                       | Ý nghĩa                                | Cách sử dụng                |
| ---------------------------- | -------------------------------------- | --------------------------- |
| `historical_gross_profit`    | Lợi nhuận gộp đã ghi nhận              | Đo đóng góp trong quá khứ   |
| `predicted_clv_12m`          | Giá trị đóng góp kỳ vọng 12 tháng      | Phân bổ ngân sách chăm sóc  |
| `purchase_intent_score`      | Điểm ý định mua                        | Trigger chiến dịch          |
| `churn_risk_score`           | Rủi ro rời bỏ                          | Chọn nhóm giữ chân          |
| `next_best_product_score`    | Điểm phù hợp sản phẩm tiếp theo        | Cá nhân hóa sản phẩm        |
| `incremental_campaign_value` | Lợi nhuận tăng thêm kỳ vọng            | Tối ưu chiến dịch           |
| `profile_quality_score`      | Độ đầy đủ, mới và đáng tin của dữ liệu | Quản trị chất lượng dữ liệu |

Với location data, chỉ sử dụng khi có cơ sở pháp lý, sự cho phép phù hợp và mục đích xử lý rõ ràng. Vị trí không nên tự động làm tăng CLV; nó chỉ nên ảnh hưởng tới dự đoán hoặc chi phí khi có bằng chứng rằng thông tin đó thực sự cải thiện kết quả kinh doanh.

Nguyên tắc thiết kế cốt lõi: Customer Profile là tập hợp dữ liệu đầu vào; CLV là giá trị kinh tế dự kiến của khách hàng; còn Incremental Business Value là giá trị mà hệ thống CDP và các quyết định dựa trên dữ liệu thực sự tạo thêm cho doanh nghiệp.