"""OpenDART document.xml(DART XML, dart4.xsd) 형식을 본뜬 분기보고서·사업보고서 픽스처.

함정 포함: 요약재무정보 표, 주석의 부문별 손익 표(매출·영업이익·순이익 행 보유)는 손익계산서로 읽으면 안 된다.
금액 단위: 연결 손익계산서는 '백만원', 별도는 '원'.
"""
import io
import zipfile

HEAD = '<?xml version="1.0" encoding="utf-8"?>\n<DOCUMENT xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="dart4.xsd">\n'


def _row(name, *vals, te="TE"):
    cells = "".join(f'<{te} ALIGN="RIGHT"><EXTRACTION ACODE="X">{v}</EXTRACTION></{te}>' for v in vals)
    return f'<TR><TD>{name}</TD>{cells}</TR>\n'


def quarterly_xml(rcept_no="20261112000647"):
    cfs = (
        '<TABLE BORDER="0"><TBODY><TR><TD>연결 포괄손익계산서</TD></TR>'
        '<TR><TD>제 50 기 3분기 2026.01.01 부터 2026.09.30 까지</TD></TR></TBODY></TABLE>\n'
        '<P ALIGN="RIGHT">(단위 : 백만원)</P>\n'
        '<TABLE BORDER="1"><THEAD>'
        '<TR><TH ROWSPAN="2"></TH><TH COLSPAN="2">제 50 기 3분기</TH><TH COLSPAN="2">제 49 기 3분기</TH></TR>'
        '<TR><TH>3개월</TH><TH>누적</TH><TH>3개월</TH><TH>누적</TH></TR></THEAD><TBODY>\n'
        + _row("매출액", "6,500,000", "19,000,000", "6,000,000", "17,000,000")
        + _row("매출원가", "(5,000,000)", "(14,500,000)", "(4,700,000)", "(13,300,000)")
        + _row("영업이익(손실)", "900,000", "2,600,000", "850,000", "2,300,000")
        + _row("당기순이익(손실)", "700,000", "2,000,000", "600,000", "1,700,000")
        + '<TR><TD>당기순이익(손실)의 귀속</TD><TU></TU><TU></TU><TU></TU><TU></TU></TR>\n'
        + _row("지배기업의 소유주에게 귀속되는 당기순이익(손실)", "500,000", "1,500,000", "450,000", "1,250,000")
        + _row("비지배지분에 귀속되는 당기순이익(손실)", "200,000", "500,000", "150,000", "450,000")
        + '</TBODY></TABLE>\n')
    ofs = (
        '<TABLE BORDER="0"><TBODY><TR><TD>포괄손익계산서</TD></TR></TBODY></TABLE>\n'
        '<P>(단위 : 원)</P>\n'
        '<TABLE BORDER="1"><THEAD>'
        '<TR><TH ROWSPAN="2"></TH><TH COLSPAN="2">제 50 기 3분기</TH><TH COLSPAN="2">제 49 기 3분기</TH></TR>'
        '<TR><TH>3개월</TH><TH>누적</TH><TH>3개월</TH><TH>누적</TH></TR></THEAD><TBODY>\n'
        + _row("매출액", "2,000,000,000", "6,000,000,000", "1,800,000,000", "5,000,000,000")
        + _row("영업이익", "300,000,000", "900,000,000", "200,000,000", "700,000,000")
        + _row("당기순이익", "250,000,000", "700,000,000", "150,000,000", "500,000,000")
        + '</TBODY></TABLE>\n')
    body = (
        HEAD + '<DOCUMENT-NAME ACODE="11013">분기보고서</DOCUMENT-NAME>\n'
        '<COMPANY-NAME AREGCIK="00000001">테스트에어로</COMPANY-NAME>\n<BODY>\n'
        '<COVER><COVER-TITLE>분 기 보 고 서</COVER-TITLE><P>(제 50 기)&cr;사업연도 2026년 01월 01일 부터</P></COVER>\n'
        '<SECTION-1 ACLASS="MANDATORY"><TITLE ATOC="Y" AASSOCNOTE="D-0-3-0-0">III. 재무에 관한 사항</TITLE>\n'
        '<SECTION-2><TITLE ATOC="Y" AASSOCNOTE="D-0-3-1-0">1. 요약재무정보</TITLE>\n'
        '<P>(단위 : 백만원)</P><TABLE><TBODY>'
        + _row("매출액", "19,000,000", "25,000,000", "11,000,000")
        + _row("영업이익", "2,600,000", "3,000,000", "1,700,000")
        + _row("당기순이익", "2,000,000", "2,500,000", "1,000,000")
        + '</TBODY></TABLE></SECTION-2>\n'
        '<SECTION-2><TITLE ATOC="Y" AASSOCNOTE="D-0-3-2-0">2. 연결재무제표</TITLE>\n'
        '<SECTION-3><TITLE ATOC="Y" AASSOCNOTE="D-0-3-2-1">2-1. 연결 재무상태표</TITLE>'
        '<P>(단위 : 백만원)</P><TABLE><TBODY>' + _row("자산총계", "80,000,000", "75,000,000") + '</TBODY></TABLE></SECTION-3>\n'
        f'<SECTION-3><TITLE ATOC="Y" AASSOCNOTE="D-0-3-2-2">2-2. 연결 포괄손익계산서</TITLE>\n{cfs}</SECTION-3>\n'
        '<SECTION-3><TITLE ATOC="Y" AASSOCNOTE="D-0-3-2-3">2-3. 연결 자본변동표</TITLE><P>생략</P></SECTION-3>\n'
        '</SECTION-2>\n'
        '<SECTION-2><TITLE ATOC="Y" AASSOCNOTE="D-0-3-3-0">3. 연결재무제표 주석</TITLE>\n'
        '<P>부문별 손익계산서</P><P>(단위 : 백만원)</P><TABLE><TBODY>'
        + _row("매출액", "1,111", "2,222") + _row("영업이익", "111", "222") + _row("당기순이익", "11", "22")
        + '</TBODY></TABLE></SECTION-2>\n'
        '<SECTION-2><TITLE ATOC="Y" AASSOCNOTE="D-0-3-4-0">4. 재무제표</TITLE>\n'
        '<SECTION-3><TITLE ATOC="Y" AASSOCNOTE="D-0-3-4-1">4-1. 재무상태표</TITLE><P>생략</P></SECTION-3>\n'
        f'<SECTION-3><TITLE ATOC="Y" AASSOCNOTE="D-0-3-4-2">4-2. 포괄손익계산서</TITLE>\n{ofs}</SECTION-3>\n'
        '</SECTION-2>\n'
        '<SECTION-2><TITLE ATOC="Y" AASSOCNOTE="D-0-3-5-0">5. 재무제표 주석</TITLE><P>생략</P></SECTION-2>\n'
        '</SECTION-1>\n</BODY>\n</DOCUMENT>\n')
    return body


def annual_xml(rcept_no="20260316001112"):
    """사업보고서: 비교 2개 기간(당기·전기), 하위 목차 없이 '2. 연결재무제표' 안에 표 제목으로 구분."""
    tbl = (
        '<TABLE BORDER="0"><TBODY><TR><TD>연결 손익계산서</TD></TR></TBODY></TABLE>'
        '<P>(단위 : 백만원)</P><TABLE BORDER="1"><THEAD><TR><TH></TH><TH>제 49 기</TH><TH>제 48 기</TH></TR></THEAD><TBODY>'
        + _row("매출액", "25,000,000", "11,000,000")
        + _row("영업이익", "3,000,000", "1,700,000")
        + _row("당기순이익", "2,500,000", "1,000,000")
        + '<TR><TD>당기순이익의 귀속</TD><TE></TE><TE></TE></TR>'
        + _row("지배기업 소유주지분", "1,900,000", "800,000")
        + _row("비지배지분", "600,000", "200,000")
        + '</TBODY></TABLE>')
    return (HEAD + '<DOCUMENT-NAME ACODE="11011">사업보고서</DOCUMENT-NAME>\n<BODY>\n'
            '<SECTION-1><TITLE ATOC="Y">III. 재무에 관한 사항</TITLE>\n'
            '<SECTION-2><TITLE ATOC="Y">2. 연결재무제표</TITLE>\n'
            '<TABLE BORDER="0"><TBODY><TR><TD>연결 재무상태표</TD></TR></TBODY></TABLE><P>(단위 : 백만원)</P>'
            '<TABLE><TBODY>' + _row("자산총계", "80,000,000", "75,000,000") + '</TBODY></TABLE>'
            + tbl +
            '</SECTION-2>\n<SECTION-2><TITLE ATOC="Y">3. 연결재무제표 주석</TITLE><P>생략</P></SECTION-2>\n'
            '</SECTION-1>\n</BODY>\n</DOCUMENT>\n')


def zip_of(rcept_no, xml, attachments=()):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"{rcept_no}.xml", xml.encode("utf-8"))
        for i, a in enumerate(attachments):
            z.writestr(f"{rcept_no}_0076{i}.xml", a.encode("utf-8"))
    return buf.getvalue()
