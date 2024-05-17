package com.databricks.labs.remorph.parsers.tsql

import com.databricks.labs.remorph.parsers.intermediate.{TableAlias, _}
import org.scalatest.Assertion
import org.scalatest.matchers.should.Matchers
import org.scalatest.wordspec.AnyWordSpec

class TSqlAstBuilderSpec extends AnyWordSpec with TSqlParserTestCommon with Matchers {

  override protected def astBuilder: TSqlParserBaseVisitor[_] = new TSqlAstBuilder

  private def example(query: String, expectedAst: TreeNode): Assertion =
    example(query, _.tsqlFile(), expectedAst)

  "tsql visitor" should {
    "translate a simple SELECT query" in {
      example(
        query = "SELECT a FROM dbo.table_x",
        expectedAst = Project(NamedTable("dbo.table_x", Map.empty, is_streaming = false), Seq(Column("a"))))
    }
    "accept constants in selects" in {
      example(
        query = "SELECT 42, 6.4, 0x5A, 2.7E9, $40",
        expectedAst = Project(
          NoTable(),
          Seq(
            Literal(integer = Some(42)),
            Literal(float = Some(6.4f)),
            Literal(string = Some("0x5A")),
            Literal(double = Some(2.7e9)),
            Literal(string = Some("$40")))))
    }

    "translate table source items with aliases" in {
      example(
        query = "SELECT a FROM dbo.table_x AS t",
        expectedAst =
          Project(TableAlias(NamedTable("dbo.table_x", Map.empty, is_streaming = false), "t"), Seq(Column("a"))))
    }

    "infer a cross join" in {
      example(
        query = "SELECT a, b, c FROM dbo.table_x, dbo.table_y",
        expectedAst = Project(
          Join(
            NamedTable("dbo.table_x", Map.empty, is_streaming = false),
            NamedTable("dbo.table_y", Map.empty, is_streaming = false),
            None,
            CrossJoin,
            Seq.empty,
            JoinDataType(is_left_struct = false, is_right_struct = false)),
          Seq(Column("a"), Column("b"), Column("c"))))
    }
    "translate a query with a JOIN" in {
      example(
        query = "SELECT T1.A, T2.B FROM DBO.TABLE_X AS T1 INNER JOIN DBO.TABLE_Y AS T2 ON T1.A = T2.A AND T1.B = T2.B",
        expectedAst = Project(
          Join(
            TableAlias(NamedTable("DBO.TABLE_X", Map(), is_streaming = false), "T1"),
            TableAlias(NamedTable("DBO.TABLE_Y", Map(), is_streaming = false), "T2"),
            Some(And(Equals(Column("T1.A"), Column("T2.A")), Equals(Column("T1.B"), Column("T2.B")))),
            InnerJoin,
            List(),
            JoinDataType(is_left_struct = false, is_right_struct = false)),
          List(Column("T1.A"), Column("T2.B"))))
    }
    "translate a query with Multiple JOIN AND Condition" in {
      example(
        query = "SELECT T1.A, T2.B FROM DBO.TABLE_X AS T1 INNER JOIN DBO.TABLE_Y AS T2 ON T1.A = T2.A " +
          "LEFT JOIN DBO.TABLE_Z AS T3 ON T1.A = T3.A AND T1.B = T3.B",
        expectedAst = Project(
          Join(
            Join(
              TableAlias(NamedTable("DBO.TABLE_X", Map(), is_streaming = false), "T1"),
              TableAlias(NamedTable("DBO.TABLE_Y", Map(), is_streaming = false), "T2"),
              Some(Equals(Column("T1.A"), Column("T2.A"))),
              InnerJoin,
              List(),
              JoinDataType(is_left_struct = false, is_right_struct = false)),
            TableAlias(NamedTable("DBO.TABLE_Z", Map(), is_streaming = false), "T3"),
            Some(And(Equals(Column("T1.A"), Column("T3.A")), Equals(Column("T1.B"), Column("T3.B")))),
            LeftOuterJoin,
            List(),
            JoinDataType(is_left_struct = false, is_right_struct = false)),
          List(Column("T1.A"), Column("T2.B"))))
    }
    "translate a query with Multiple JOIN OR Conditions" in {
      example(
        query = "SELECT T1.A, T2.B FROM DBO.TABLE_X AS T1 INNER JOIN DBO.TABLE_Y AS T2 ON T1.A = T2.A " +
          "LEFT JOIN DBO.TABLE_Z AS T3 ON T1.A = T3.A OR T1.B = T3.B",
        expectedAst = Project(
          Join(
            Join(
              TableAlias(NamedTable("DBO.TABLE_X", Map(), is_streaming = false), "T1"),
              TableAlias(NamedTable("DBO.TABLE_Y", Map(), is_streaming = false), "T2"),
              Some(Equals(Column("T1.A"), Column("T2.A"))),
              InnerJoin,
              List(),
              JoinDataType(is_left_struct = false, is_right_struct = false)),
            TableAlias(NamedTable("DBO.TABLE_Z", Map(), is_streaming = false), "T3"),
            Some(Or(Equals(Column("T1.A"), Column("T3.A")), Equals(Column("T1.B"), Column("T3.B")))),
            LeftOuterJoin,
            List(),
            JoinDataType(is_left_struct = false, is_right_struct = false)),
          List(Column("T1.A"), Column("T2.B"))))
    }
    "translate a query with a RIGHT OUTER JOIN" in {
      example(
        query = "SELECT T1.A FROM DBO.TABLE_X AS T1 RIGHT OUTER JOIN DBO.TABLE_Y AS T2 ON T1.A = T2.A",
        expectedAst = Project(
          Join(
            TableAlias(NamedTable("DBO.TABLE_X", Map(), is_streaming = false), "T1"),
            TableAlias(NamedTable("DBO.TABLE_Y", Map(), is_streaming = false), "T2"),
            Some(Equals(Column("T1.A"), Column("T2.A"))),
            RightOuterJoin,
            List(),
            JoinDataType(is_left_struct = false, is_right_struct = false)),
          List(Column("T1.A"))))
    }
    "translate a query with a FULL OUTER JOIN" in {
      example(
        query = "SELECT T1.A FROM DBO.TABLE_X AS T1 FULL OUTER JOIN DBO.TABLE_Y AS T2 ON T1.A = T2.A",
        expectedAst = Project(
          Join(
            TableAlias(NamedTable("DBO.TABLE_X", Map(), is_streaming = false), "T1"),
            TableAlias(NamedTable("DBO.TABLE_Y", Map(), is_streaming = false), "T2"),
            Some(Equals(Column("T1.A"), Column("T2.A"))),
            FullOuterJoin,
            List(),
            JoinDataType(is_left_struct = false, is_right_struct = false)),
          List(Column("T1.A"))))
    }
  }
}
