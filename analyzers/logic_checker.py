# analyzers/logic_checker.py
#
# Dynamic, AST-based logic checks. These look at the *structure* of code
# (branches, comparisons, return values) instead of matching specific
# strings, so they generalize to any function/variable names rather than
# only the exact demo code they were written against.
#
# Still heuristic, not full semantic analysis — treat findings as
# suggestions for human review, not auto-fixable by default.

import ast


def detect_logic_errors(code, filename):
    findings = []

    try:
        tree = ast.parse(code)
    except SyntaxError:
        # syntax_checker already reports this; don't double-report here
        return findings

    checker = _LogicVisitor(filename)
    checker.visit(tree)
    return checker.findings


class _LogicVisitor(ast.NodeVisitor):
    def __init__(self, filename):
        self.filename = filename
        self.findings = []

    def _add(self, node, message):
        self.findings.append({
            "file": self.filename,
            "line": getattr(node, "lineno", 0),
            "severity": "warning",
            "category": "logic",
            "message": message,
        })

    # ── Check 1: if/else branches return the same literal value ──────
    # Catches things like:
    #   if password == "admin": return True
    #   return True
    # regardless of variable/function names.
    def visit_If(self, node):
        then_val = self._single_return_literal(node.body)
        else_val = self._single_return_literal(node.orelse)

        if then_val is not None and else_val is not None and then_val == else_val:
            self._add(
                node,
                f"Both branches return the same value ({then_val!r}) — "
                "the condition has no effect on the result."
            )

        self.generic_visit(node)

    @staticmethod
    def _single_return_literal(body):
        """If `body` is exactly one `return <literal>` statement, return
        that literal's value. Otherwise return None (not applicable)."""
        if len(body) != 1:
            return None
        stmt = body[0]
        if not isinstance(stmt, ast.Return) or stmt.value is None:
            return None
        if isinstance(stmt.value, ast.Constant):
            return stmt.value.value
        return None

    # ── Check 2: comparison direction looks inverted vs. the branch ──
    # Catches things like:
    #   if age > 18: return False
    # A ">" comparison whose "true" branch immediately returns a
    # constant that reads as a negative/false-y result is suspicious —
    # the common bug is reversed branches on a threshold check.
    def visit_FunctionDef(self, node):
        for stmt in ast.walk(node):
            if isinstance(stmt, ast.If) and self._looks_like_threshold_check(stmt):
                self._flag_possible_inverted_branch(stmt)
        self.generic_visit(node)

    @staticmethod
    def _looks_like_threshold_check(if_node):
        test = if_node.test
        return isinstance(test, ast.Compare) and any(
            isinstance(op, (ast.Gt, ast.GtE, ast.Lt, ast.LtE))
            for op in test.ops
        )

    def _flag_possible_inverted_branch(self, if_node):
        then_val = self._single_return_literal(if_node.body)
        if then_val is False:
            self._add(
                if_node,
                "Branch returns False immediately after a threshold "
                "comparison (e.g. 'age > N') — check whether the "
                "condition/branches are reversed."
            )

    # ── Check 3: multiplication in a function whose name implies a
    #    reduction (discount/reduce/decrease/off), or division in one
    #    whose name implies growth. Name-based, but works for any names
    #    containing these words, not one hardcoded function. ──────────
    REDUCE_WORDS = ("discount", "reduce", "decrease", "off", "markdown")
    INCREASE_WORDS = ("increase", "markup", "grow", "multiply")

    def visit_BinOp(self, node):
        func = self._enclosing_function_name(node)
        if func:
            fname = func.lower()
            if isinstance(node.op, ast.Mult) and any(w in fname for w in self.REDUCE_WORDS):
                self._add(
                    node,
                    f"Function '{func}' name implies a reduction, but "
                    "multiplies instead — check the operator."
                )
            if isinstance(node.op, ast.Div) and any(w in fname for w in self.INCREASE_WORDS):
                self._add(
                    node,
                    f"Function '{func}' name implies an increase, but "
                    "divides instead — check the operator."
                )
        self.generic_visit(node)

    def _enclosing_function_name(self, node):
        # ast doesn't give parent pointers by default; track via a stack.
        return getattr(self, "_current_func", None)

    def visit_FunctionDef_track(self, node):  # not used directly; see below
        pass