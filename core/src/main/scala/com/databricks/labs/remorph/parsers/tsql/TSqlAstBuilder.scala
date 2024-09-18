package com.databricks.labs.remorph.parsers.tsql

import com.databricks.labs.remorph.parsers.tsql.TSqlParser.DmlClauseContext
import com.databricks.labs.remorph.parsers.{intermediate => ir}

import scala.collection.JavaConverters.asScalaBufferConverter

/**
 * @see
 *   org.apache.spark.sql.catalyst.parser.AstBuilder
 */
class TSqlAstBuilder extends TSqlParserBaseVisitor[ir.LogicalPlan] {

  private val relationBuilder = new TSqlRelationBuilder
  private val expressionBuilder = new TSqlExpressionBuilder
  private val optionBuilder = new OptionBuilder(expressionBuilder)
  private val ddlBuilder = new TSqlDDLBuilder(optionBuilder, expressionBuilder, relationBuilder)

  override def visitTSqlFile(ctx: TSqlParser.TSqlFileContext): ir.LogicalPlan = {
    Option(ctx.batch()).map(_.accept(this)).getOrElse(ir.Batch(List()))
  }

  override def visitBatch(ctx: TSqlParser.BatchContext): ir.LogicalPlan = {
    val executeBodyBatchPlan = Option(ctx.executeBodyBatch()).map(_.accept(this))
    val sqlClausesPlans = ctx.sqlClauses().asScala.map(_.accept(this)).collect { case p: ir.LogicalPlan => p }

    executeBodyBatchPlan match {
      case Some(plan) => ir.Batch(plan :: sqlClausesPlans.toList)
      case None => ir.Batch(sqlClausesPlans.toList)
    }
  }

  // TODO: Stored procedure calls etc as batch start
  override def visitExecuteBodyBatch(ctx: TSqlParser.ExecuteBodyBatchContext): ir.LogicalPlan =
    ir.UnresolvedRelation(ctx.getText)

  override def visitSqlClauses(ctx: TSqlParser.SqlClausesContext): ir.LogicalPlan = {
    ctx match {
      case dml if dml.dmlClause() != null => dml.dmlClause().accept(this)
      case cfl if cfl.cflStatement() != null => cfl.cflStatement().accept(this)
      case another if another.anotherStatement() != null => another.anotherStatement().accept(this)
      case ddl if ddl.ddlClause() != null => ddl.ddlClause().accept(ddlBuilder)
      case dbcc if dbcc.dbccClause() != null => dbcc.dbccClause().accept(this)
      case backup if backup.backupStatement() != null => backup.backupStatement().accept(this)
      case coaFunction if coaFunction.createOrAlterFunction() != null =>
        coaFunction.createOrAlterFunction().accept(this)
      case coaProcedure if coaProcedure.createOrAlterProcedure() != null =>
        coaProcedure.createOrAlterProcedure().accept(this)
      case coaTrigger if coaTrigger.createOrAlterTrigger() != null => coaTrigger.createOrAlterTrigger().accept(this)
      case cv if cv.createView() != null => cv.createView().accept(this)
      case go if go.goStatement() != null => go.goStatement().accept(this)
      case _ => ir.UnresolvedRelation(ctx.getText)
    }
  }

  override def visitDmlClause(ctx: DmlClauseContext): ir.LogicalPlan = {
    val query = ctx match {
      case insert if insert.insert() != null => insert.insert().accept(relationBuilder)
      case select if select.selectStatement() != null =>
        select.selectStatement.accept(relationBuilder)
      case delete if delete.delete() != null => delete.delete().accept(relationBuilder)
      case merge if merge.merge() != null => merge.merge().accept(relationBuilder)
      case update if update.update() != null => update.update().accept(relationBuilder)
      case bulk if bulk.bulkStatement() != null => bulk.bulkStatement().accept(relationBuilder)
      case _ => ir.UnresolvedRelation(ctx.getText)
    }
    Option(ctx.withExpression())
      .map { withExpression =>
        val ctes = withExpression.commonTableExpression().asScala.map(_.accept(relationBuilder))
        ir.WithCTE(ctes, query)
      }
      .getOrElse(query)
  }

  /**
   * This is not actually implemented but was a quick way to exercise the genericOption builder before we had other
   * syntax implemented to test it with.
   *
   * @param ctx
   *   the parse tree
   */
  override def visitBackupStatement(ctx: TSqlParser.BackupStatementContext): ir.LogicalPlan = {
    ctx.backupDatabase().accept(this)
  }

  override def visitBackupDatabase(ctx: TSqlParser.BackupDatabaseContext): ir.LogicalPlan = {
    val database = ctx.id().getText
    val opts = ctx.optionList()
    val options = opts.asScala.flatMap(_.genericOption().asScala).toList.map(optionBuilder.buildOption)
    val (disks, boolFlags, autoFlags, values) = options.foldLeft(
      (List.empty[String], Map.empty[String, Boolean], List.empty[String], Map.empty[String, ir.Expression])) {
      case ((disks, boolFlags, autoFlags, values), option) =>
        option match {
          case ir.OptionString("DISK", value) =>
            (value.stripPrefix("'").stripSuffix("'") :: disks, boolFlags, autoFlags, values)
          case ir.OptionOn(id) => (disks, boolFlags + (id -> true), autoFlags, values)
          case ir.OptionOff(id) => (disks, boolFlags + (id -> false), autoFlags, values)
          case ir.OptionAuto(id) => (disks, boolFlags, id :: autoFlags, values)
          case ir.OptionExpression(id, expr, _) => (disks, boolFlags, autoFlags, values + (id -> expr))
          case _ => (disks, boolFlags, autoFlags, values)
        }
    }
    // Default flags generally don't need to be specified as they are by definition, the default
    BackupDatabase(database, disks, boolFlags, autoFlags, values)
  }
}
