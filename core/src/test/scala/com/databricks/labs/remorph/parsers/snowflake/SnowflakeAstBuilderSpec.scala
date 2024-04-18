package com.databricks.labs.remorph.parsers.snowflake

import com.databricks.labs.remorph.parsers.intermediate.{Alias, Column, FullOuterJoin, InnerJoin, Join, JoinDataType, LeftOuterJoin, NamedTable, Project, RightOuterJoin, TreeNode}
import org.antlr.v4.runtime.{CharStreams, CommonTokenStream}
import org.scalatest.Assertion
import org.scalatest.matchers.should.Matchers
import org.scalatest.wordspec.AnyWordSpec

class SnowflakeAstBuilderSpec extends AnyWordSpec with Matchers {

  private def parseString(input: String): SnowflakeParser.Snowflake_fileContext = {
    val inputString = CharStreams.fromString(input)
    val lexer = new SnowflakeLexer(inputString)
    val tokenStream = new CommonTokenStream(lexer)
    val parser = new SnowflakeParser(tokenStream)
    val tree = parser.snowflake_file()
    // uncomment the following line if you need a peek in the Snowflake AST
    // println(tree.toStringTree(parser))
    tree
  }

  private def example(query: String, expectedAst: TreeNode): Assertion = {
    val sfTree = parseString(query)

    val result = new SnowflakeAstBuilder().visit(sfTree)

    result shouldBe expectedAst
  }

  "SnowflakeVisitor" should {
    "translate a simple SELECT query" in {
      example(
        query = "SELECT a FROM b",
        expectedAst = Project(NamedTable("b", Map.empty, is_streaming = false), Seq(Column("a"))))
    }

    "translate a simple SELECT query with an aliased column" in {

      example(
        query = "SELECT a AS aa FROM b",
        expectedAst =
          Project(NamedTable("b", Map.empty, is_streaming = false), Seq(Alias(Column("a"), Seq("aa"), None))))
    }

    "translate a simple SELECT query involving multiple columns" in {

      example(
        query = "SELECT a, b, c FROM table_x",
        expectedAst =
          Project(NamedTable("table_x", Map.empty, is_streaming = false), Seq(Column("a"), Column("b"), Column("c"))))
    }

    "translate a SELECT query involving multiple columns and aliases" in {

      example(
        query = "SELECT a, b AS bb, c FROM table_x",
        expectedAst = Project(
          NamedTable("table_x", Map.empty, is_streaming = false),
          Seq(Column("a"), Alias(Column("b"), Seq("bb"), None), Column("c"))))
    }

    val simpleJoinAst =
      Join(
        NamedTable("table_x", Map.empty, is_streaming = false),
        NamedTable("table_y", Map.empty, is_streaming = false),
        join_condition = None,
        InnerJoin,
        using_columns = Seq(),
        JoinDataType(is_left_struct = false, is_right_struct = false))

    "translate a query with a JOIN" in {
      example(query = "SELECT a FROM table_x JOIN table_y", expectedAst = Project(simpleJoinAst, Seq(Column("a"))))
    }

    // TODO: fix the grammar (LEFT gets parsed as an alias rather than a join_type)
    "translate a query with a LEFT JOIN" ignore {
      example(
        query = "SELECT a FROM table_x LEFT JOIN table_y",
        expectedAst = Project(simpleJoinAst.copy(join_type = LeftOuterJoin), Seq(Column("a"))))
    }

    "translate a query with a LEFT OUTER JOIN" in {
      example(
        query = "SELECT a FROM table_x LEFT OUTER JOIN table_y",
        expectedAst = Project(simpleJoinAst.copy(join_type = LeftOuterJoin), Seq(Column("a"))))
    }

    // TODO: fix the grammar (RIGHT gets parsed as an alias rather than a join_type)
    "translate a query with a RIGHT JOIN" ignore {
      example(
        query = "SELECT a FROM table_x RIGHT JOIN table_y",
        expectedAst = Project(simpleJoinAst.copy(join_type = RightOuterJoin), Seq(Column("a"))))
    }

    "translate a query with a RIGHT OUTER JOIN" in {
      example(
        query = "SELECT a FROM table_x RIGHT OUTER JOIN table_y",
        expectedAst = Project(simpleJoinAst.copy(join_type = RightOuterJoin), Seq(Column("a"))))
    }

    "translate a query with a FULL JOIN" in {
      example(
        query = "SELECT a FROM table_x FULL JOIN table_y",
        expectedAst = Project(simpleJoinAst.copy(join_type = FullOuterJoin), Seq(Column("a"))))
    }



  }
}
