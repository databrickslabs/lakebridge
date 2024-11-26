package com.databricks.labs.remorph.parsers.snowflake.rules

import com.databricks.labs.remorph.intermediate.{Expression, _}

class Dealiaser(val aliases: Map[String, Expression], val isInColumnExpression: Boolean = false) {

  def dealiasProject(project: Project): Project = {
    val input = dealiasInput(project.input)
    val columns = dealiasExpressions(project.columns)
    project.makeCopy(Array(input, columns)).asInstanceOf[Project]
  }

  private def dealiasInput(input: LogicalPlan): LogicalPlan = {
    input match {
      case filter: Filter => dealiasFilter(filter)
      case _: LogicalPlan => input
    }
  }

  private def dealiasFilter(filter: Filter): Filter = {
    val transformed = dealiasExpression(filter.condition)
    filter.makeCopy(Array(filter.input, transformed)).asInstanceOf[Filter]
  }

  private def dealiasExpressions(expressions: Seq[Expression]): Seq[Expression] = {
    expressions map { dealiasExpression }
  }

  private def dealiasExpression(expression: Expression): Expression = {
    expression match {
      case alias: Alias => dealiasAlias(alias)
      case name: Name => dealiasName(name)
      case id: Id => dealiasId(id)
      case in: In => dealiasIn(in)
      case unary: Unary => dealiasUnary(unary)
      case binary: Binary => dealiasBinary(binary)
      case func: CallFunction => dealiasCallFunction(func)
      case window: Window => dealiasWindow(window)
      case subquery: SubqueryExpression => dealiasSubqueryExpression(subquery)
      case expression: Expression => expression
    }
  }

  private def dealiasAlias(alias: Alias): Alias = {
    val filtered = aliases - alias.name.id
    val expression = new Dealiaser(filtered, true).dealiasExpression(alias.child)
    alias.makeCopy(Array(expression.asInstanceOf[AnyRef], alias.name.asInstanceOf[AnyRef])).asInstanceOf[Alias]
  }

  private def dealiasName(name: Name): Expression = {
    // don't dealias column names when dealing with column expressions
    if (isInColumnExpression) {
      name
    } else {
      val alias = aliases.find(p => p._1 == name.name)
      if (alias.isEmpty) {
        name
      } else {
        val filtered = aliases - name.name
        new Dealiaser(filtered).dealiasExpression(alias.get._2)
      }
    }
  }

  private def dealiasId(id: Id): Expression = {
    // don't dealias column names when dealing with column expressions
    if (isInColumnExpression) {
      id
    } else {
      val alias = aliases.find(p => p._1 == id.id)
      if (alias.isEmpty) {
        id
      } else {
        val filtered = aliases - id.id
        new Dealiaser(filtered).dealiasExpression(alias.get._2)
      }
    }
  }

  private def dealiasIn(in: In): Expression = {
    val left = dealiasExpression(in.left)
    val other = dealiasExpressions(in.other)
    in.makeCopy(Array(left, other))
  }

  private def dealiasUnary(unary: Unary): Expression = {
    val child = dealiasExpression(unary.child)
    unary.makeCopy(Array(child))
  }

  private def dealiasBinary(binary: Binary): Expression = {
    val head = dealiasExpression(binary.children.head)
    val last = dealiasExpression(binary.children.last)
    binary.makeCopy(Array(head, last))
  }

  private def dealiasCallFunction(func: CallFunction): CallFunction = {
    val args = func.arguments map { dealiasExpression }
    func.makeCopy(Array(func.function_name, args)).asInstanceOf[CallFunction]
  }

  private def dealiasWindow(window: Window): Expression = {
    if (isInColumnExpression) {
      // window expressions need to be dealiased, so switch to dealiasing behavior
      new Dealiaser(aliases).dealiasWindow(window)
    } else {
      val partition = dealiasExpressions(window.partition_spec)
      val sort_order = dealiasSortOrders(window.sort_order)
      window.makeCopy(
        Array(
          window.window_function.asInstanceOf[AnyRef],
          partition.asInstanceOf[AnyRef],
          sort_order.asInstanceOf[AnyRef],
          window.frame_spec.asInstanceOf[AnyRef],
          window.ignore_nulls.asInstanceOf[AnyRef]))
    }
  }

  private def dealiasSubqueryExpression(subquery: SubqueryExpression): Expression = {
    val plan = subquery.plan match {
      case project: Project => {
        val aliases = Dealiaser.collectAliases(project.columns)
        if (aliases.isEmpty) {
          project
        } else {
          val dealiaser = new Dealiaser(aliases)
          dealiaser.dealiasProject(project)
        }
      }
      case plan: LogicalPlan => plan // TODO log or raise error ?
    }
    subquery.makeCopy(Array(plan))
  }

  private def dealiasSortOrders(sort_order: Seq[SortOrder]): Seq[SortOrder] = {
    sort_order map { sort => dealiasSortOrder(sort) }
  }

  private def dealiasSortOrder(sort_order: SortOrder): SortOrder = {
    val transformed = dealiasExpression(sort_order.child)
    sort_order.makeCopy(Array(transformed, sort_order.direction, sort_order.nullOrdering)).asInstanceOf[SortOrder]
  }

}

object Dealiaser {

  def collectAliases(columns: Seq[Expression]): Map[String, Expression] = {
    columns.collect { case Alias(e, name) if !e.isInstanceOf[Literal] => name.id -> e }.toMap
  }
}

class DealiasLCAs extends Rule[LogicalPlan] with IRHelpers {

  override def apply(plan: LogicalPlan): LogicalPlan = {
    plan transform { case project: Project =>
      dealiasProject(project)
    }
  }

  private def dealiasProject(project: Project): Project = {
    val aliases = Dealiaser.collectAliases(project.columns)
    if (aliases.isEmpty) {
      project
    } else {
      val dealiaser = new Dealiaser(aliases)
      dealiaser.dealiasProject(project)
    }
  }

  private def collectAliases(columns: Seq[Expression]): Map[String, Expression] = {
    columns.collect { case Alias(e, name) if !e.isInstanceOf[Literal] => name.id -> e }.toMap
  }

}
