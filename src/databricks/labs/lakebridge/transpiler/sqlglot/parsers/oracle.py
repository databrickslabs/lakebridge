from sqlglot.dialects.oracle import Oracle as Orc
from sqlglot.tokens import TokenType


class Oracle(Orc):
    # Instantiate Oracle Dialect
    oracle = Orc()

    class Tokenizer(Orc.Tokenizer):
        KEYWORDS = {
            **Orc.Tokenizer.KEYWORDS,
            'LONG': TokenType.TEXT,
            'NCLOB': TokenType.TEXT,
            'ROWID': TokenType.TEXT,
            'UROWID': TokenType.TEXT,
            'ANYTYPE': TokenType.TEXT,
            'ANYDATA': TokenType.TEXT,
            'ANYDATASET': TokenType.TEXT,
            'XMLTYPE': TokenType.TEXT,
            'SDO_GEOMETRY': TokenType.TEXT,
            'SDO_TOPO_GEOMETRY': TokenType.TEXT,
            'SDO_GEORASTER': TokenType.TEXT,
        }
