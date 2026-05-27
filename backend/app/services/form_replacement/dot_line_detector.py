"""
Dot Line Detector - Phát hiện và trích xuất các placeholder bằng dòng chấm
"""

import re
from typing import List, Dict, Tuple
from dataclasses import dataclass


@dataclass
class DotLinePlaceholder:
    """Model cho một dot-line placeholder"""
    text: str  # Full text với dot-line (e.g., "Họ tên: .......")
    label: str  # Label/prompt (e.g., "Họ tên")
    dot_start_pos: int  # Vị trí bắt đầu của dot-line
    dot_end_pos: int  # Vị trí kết thúc của dot-line
    line_index: int  # Index của line trong document
    field_name: str  # Generated field name
    has_prefix: bool  # Có text trước dot-line không
    has_suffix: bool  # Có text sau dot-line không

    def to_dict(self):
        return {
            'text': self.text,
            'label': self.label,
            'dot_start_pos': self.dot_start_pos,
            'dot_end_pos': self.dot_end_pos,
            'line_index': self.line_index,
            'field_name': self.field_name,
            'has_prefix': self.has_prefix,
            'has_suffix': self.has_suffix
        }


class DotLineDetector:
    """Detect và extract dot-line placeholders từ document"""

    # Các pattern để match dot-lines
    DOT_PATTERNS = [
        r'\.{2,}',  # ..... (2 hoặc nhiều dấu chấm)
        r'_{2,}',   # _____ (2 hoặc nhiều underscores)
        r'-{2,}',   # ----- (2 hoặc nhiều dashes)
        r'─{2,}',   # ─────
        r'={2,}',   # ===== (form fill lines, e.g. "Họ và tên =====")
    ]

    # Field type keywords
    FIELD_TYPE_KEYWORDS = {
        'năm': 'number',
        'ngày': 'date',
        'điện thoại': 'phone',
        'email': 'email',
        'số điện thoại': 'phone',
        'ngài sinh': 'date',
        'năm sinh': 'number',
    }

    @staticmethod
    def _is_dot_line_pattern(text: str) -> bool:
        """Check nếu text có chứa dot-line pattern"""
        for pattern in DotLineDetector.DOT_PATTERNS:
            if re.search(pattern, text):
                return True
        return False

    @staticmethod
    def _extract_label_from_text(text: str) -> str:
        """Extract label từ text có chứa dot-lines
        
        Examples:
            "Họ tên: ......." → "Họ tên"
            "Tôi là ....... và tôi muốn" → "Tôi là"
        """
        text = text.strip()
        
        # Pattern 1: "Label: ........" or "Label ========"
        match = re.match(r'^([^._{}\-─=]+?)[:：]\s*[._{}\-─=]+', text)
        if match:
            return match.group(1).strip()
        
        # Pattern 2: "Text ....... more text"
        match = re.match(r'^(.+?)\s+[._{}\-─=]+(?:\s+|$)', text)
        if match:
            return match.group(1).strip()
        
        # Fallback: lấy text trước pattern
        pattern_match = re.search(r'[._{}\-─=]+', text)
        if pattern_match:
            before_text = text[:pattern_match.start()].strip()
            if before_text:
                return before_text
        
        return text.strip()

    @staticmethod
    def _detect_field_type(label: str) -> str:
        """Phát hiện field type từ label"""
        label_lower = label.lower()
        
        for keyword, field_type in DotLineDetector.FIELD_TYPE_KEYWORDS.items():
            if keyword in label_lower:
                return field_type
        
        return 'text'

    @staticmethod
    def _generate_field_name(label: str) -> str:
        """Generate field name từ label (snake_case)"""
        field_name = label.lower()
        field_name = re.sub(r'[^a-ỿ0-9\s]', '', field_name)
        field_name = field_name.strip()
        field_name = '_'.join(field_name.split())
        return field_name[:50]  # Max 50 chars

    @staticmethod
    def detect_placeholders(text: str, line_index: int = 0) -> List[DotLinePlaceholder]:
        """Detect tất cả dot-line placeholders trong một line"""
        
        if not DotLineDetector._is_dot_line_pattern(text):
            return []
        
        placeholders = []
        
        # Find all dot-line patterns
        for pattern in DotLineDetector.DOT_PATTERNS:
            matches = list(re.finditer(pattern, text))
            
            for match in matches:
                start_pos = match.start()
                end_pos = match.end()
                
                # Extract label
                label = DotLineDetector._extract_label_from_text(text)
                
                # Check có prefix/suffix không
                has_prefix = start_pos > 0
                has_suffix = end_pos < len(text)
                
                # Generate field name
                field_name = DotLineDetector._generate_field_name(label)
                
                placeholder = DotLinePlaceholder(
                    text=text,
                    label=label,
                    dot_start_pos=start_pos,
                    dot_end_pos=end_pos,
                    line_index=line_index,
                    field_name=field_name,
                    has_prefix=has_prefix,
                    has_suffix=has_suffix
                )
                
                placeholders.append(placeholder)
        
        return placeholders

    @staticmethod
    def detect_from_document(doc) -> List[DotLinePlaceholder]:
        """Detect placeholders từ toàn bộ document"""
        all_placeholders = []
        
        try:
            # Try DOCX paragraphs
            for line_idx, para in enumerate(doc.paragraphs):
                text = para.text.strip()
                if text:
                    placeholders = DotLineDetector.detect_placeholders(text, line_idx)
                    all_placeholders.extend(placeholders)
        except AttributeError:
            pass
        
        try:
            # Try tables
            for table_idx, table in enumerate(doc.tables):
                cell_idx = 0
                for row in table.rows:
                    for cell in row.cells:
                        text = cell.text.strip()
                        if text:
                            placeholders = DotLineDetector.detect_placeholders(text, table_idx * 1000 + cell_idx)
                            all_placeholders.extend(placeholders)
                        cell_idx += 1
        except AttributeError:
            pass
        
        return all_placeholders

    @staticmethod
    def extract_fields(placeholders: List[DotLinePlaceholder]) -> List[Dict]:
        """Extract field info từ placeholders"""
        fields = []
        seen_labels = set()
        
        for idx, placeholder in enumerate(placeholders):
            # Skip duplicate labels
            if placeholder.label in seen_labels:
                continue
            seen_labels.add(placeholder.label)
            
            field = {
                'name': placeholder.field_name,
                'label': placeholder.label,
                'field_type': DotLineDetector._detect_field_type(placeholder.label),
                'order': idx,
                'placeholder': placeholder.to_dict()
            }
            fields.append(field)
        
        return fields
