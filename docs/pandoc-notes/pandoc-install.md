

---
title: "Hướng dẫn cài đặt và sử dụng Pandoc trên Ubuntu"
subtitle: "Biên dịch tài liệu Markdown sang PDF / HTML / Slides cho dự án Leo Customer 360"
author: "Leo Customer 360 Team"
date: 2026-08-04
geometry: "a4paper,margin=1.7cm"
fontsize: "9.5pt"
mainfont: "DejaVu Serif"
---

# Hướng dẫn Cài đặt và Sử dụng Pandoc (Ubuntu Linux)

Tài liệu này hướng dẫn cách cài đặt, cấu hình và sử dụng **Pandoc** kết hợp **XeLaTeX** trên môi trường Ubuntu Linux để biên dịch các tệp tài liệu Markdown trong thư mục `docs/` của dự án thành định dạng **PDF**, **HTML**, và **Slides**.

---

## 1. Yêu cầu Hệ thống & Các Gói Cần Cài đặt

Để Pandoc có thể xuất tài liệu PDF đẹp mắt, hỗ trợ đầy đủ tiếng Việt (Unicode) và định dạng bảng chuẩn A4, hệ thống cần cài đặt Pandoc cùng engine XeLaTeX và phông chữ Unicode (như DejaVu / Noto Fonts).

### 1.1 Cài đặt các gói Ubuntu (apt)

Mở terminal trên Ubuntu và chạy các lệnh sau:

```bash
#!/usr/bin/env bash
set -euo pipefail

echo "==> Updating package index..."
sudo apt update

echo "==> Upgrading installed packages..."
sudo apt full-upgrade -y

echo "==> Installing Pandoc, XeLaTeX, required LaTeX packages, and system fonts..."
sudo apt install -y \
    pandoc \
    texlive-xetex \
    texlive-latex-recommended \
    texlive-latex-extra \
    texlive-science \
    texlive-fonts-recommended \
    texlive-fonts-extra \
    texlive-plain-generic \
    texlive-bibtex-extra \
    lmodern \
    fonts-dejavu \
    fonts-noto \
    fonts-noto-cjk \
    fonts-noto-color-emoji \
    fonts-symbola
    

echo
echo "==> Versions"
pandoc --version | head -n 2
echo
xelatex --version | head -n 2
```

---

## 2. Cấu hình Font và Tiền xử lý Markdown

Để tránh lỗi font chữ tiếng Việt (bị lỗi ô vuông hoặc dấu hỏi khi render PDF) và tràn lề bảng trên trang A4, các file Markdown trong thư mục `docs/` nên có khối **YAML Frontmatter** ở đầu file như sau:

```yaml
---
title: "Tên tài liệu"
subtitle: "Mô tả ngắn gọn"
author: "Đội ngũ Phát triển"
date: 2026-08-04
geometry: "a4paper,margin=1.7cm"
fontsize: "9.5pt"
linestretch: "1.0"
mainfont: "DejaVu Serif"
---
```

### Các thông số Frontmatter chính:
*   `geometry`: Cấu hình lề trang A4 (`margin=1.7cm` giúp tối ưu diện tích hiển thị bảng biểu).
*   `mainfont`: Phông chữ hỗ trợ tiếng Việt đầy đủ (`DejaVu Serif` hoặc `Noto Serif`).
*   `fontsize`: Kích thước chữ chuẩn cho tài liệu kỹ thuật (`9.5pt` hoặc `10pt`).

---

## 3. Lệnh biên dịch Pandoc cho Dự án

Tất cả các lệnh dưới đây được chạy từ thư mục gốc của repository: `/home/thomas/0-uspa/leo-customer360`.

### 3.1 Biên dịch Markdown sang PDF (Dùng XeLaTeX Engine)

Biên dịch tài liệu kỹ thuật `docs/identity-resolution.md` sang PDF:

```bash
pandoc docs/identity-resolution.md \
    --pdf-engine=xelatex \
    -o docs/identity-resolution-paper.pdf
```

Biên dịch tài liệu Slides `docs/identity-resolution-slide.md` sang PDF presentation (Beamer):

```bash
pandoc docs/identity-resolution-slide.md \
    -t beamer \
    --pdf-engine=xelatex \
    -o docs/identity-resolution-slide.pdf
```

### 3.2 Biên dịch Markdown sang HTML

Biên dịch tệp Markdown thành trang HTML tự chứa (standalone) bao gồm cả CSS:

```bash
pandoc docs/identity-resolution.md \
    -s --self-contained \
    -o docs/identity-resolution.html
```

### 3.3 Biên dịch hàng loạt tất cả tài liệu trong `docs/`

Bạn có thể dùng lệnh bash đơn giản để biên dịch toàn bộ tài liệu Markdown trong thư mục `docs/` sang PDF:

```bash
for file in docs/*.md; do
    if [ "$file" != "docs/README.md" ]; then
        echo "Processing $file..."
        pandoc "$file" --pdf-engine=xelatex -o "${file%.md}.pdf" 2>/dev/null || echo "Skipped or failed $file"
    fi
done
```

---

## 4. Giải quyết các sự cố thường gặp (Troubleshooting)

### 4.1 Lỗi: `pdfEngine xelatex is not found`
*   **Nguyên nhân:** Chưa cài đặt gói `texlive-xetex`.
*   **Khắc phục:** Chạy `sudo apt install -y texlive-xetex`.

### 4.2 Lỗi: Font tiếng Việt bị lỗi chữ hoặc không hiển thị
*   **Nguyên nhân:** Thiếu phông chữ `DejaVu Serif` hoặc chưa chỉ định `mainfont`.
*   **Khắc phục:** Chạy `sudo apt install -y fonts-dejavu` và đảm bảo YAML Frontmatter có trường `mainfont: "DejaVu Serif"`.

### 4.3 Bảng Markdown bị đè hoặc tràn ra ngoài lề trang PDF A4
*   **Nguyên nhân:** Chuỗi text quá dài trong một ô hoặc bảng có quá nhiều cột mà không tự ngắt dòng.
*   **Khắc phục:**
    1. Cắt ngắn nội dung các chuỗi hex/hash dài (ví dụ: `9f86d08188...15b0f00a08`).
    2. Viết lại tiêu đề các cột ngắn gọn hơn.
    3. Đảm bảo cấu hình lề trong YAML Frontmatter là `geometry: "a4paper,margin=1.7cm"`.

---

## 5. Tóm tắt nhanh Cheat Sheet

| Thao tác | Câu lệnh Terminal |
| :--- | :--- |
| **Cài đặt đầy đủ** | `sudo apt install -y pandoc texlive-xetex texlive-fonts-recommended fonts-dejavu` |
| **Xuất PDF chuẩn A4** | `pandoc docs/identity-resolution.md --pdf-engine=xelatex -o identity-resolution.pdf` |
| **Xuất HTML** | `pandoc docs/identity-resolution.md -s -o identity-resolution.html` |
| **Xuất Slide Beamer** | `pandoc docs/identity-resolution-slide.md -t beamer --pdf-engine=xelatex -o slide.pdf` |