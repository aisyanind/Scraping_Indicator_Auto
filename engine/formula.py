import ast
import operator


BINARY = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
}

UNARY = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def calculate_formula(expression, variables):
    expression = str(expression or "").strip() or "S1"
    tree = ast.parse(expression, mode="eval")

    def evaluate(node):
        if isinstance(node, ast.Expression):
            return evaluate(node.body)

        if isinstance(node, ast.Constant) and isinstance(
            node.value, (int, float)
        ):
            return node.value

        if isinstance(node, ast.Name):
            if node.id not in variables:
                raise ValueError(f"Variabel {node.id} tidak ditemukan")
            value = variables[node.id]
            if value is None:
                raise ValueError(f"Variabel {node.id} kosong")
            return float(value)

        if isinstance(node, ast.BinOp):
            op_type = type(node.op)
            if op_type not in BINARY:
                raise ValueError("Operator formula tidak diizinkan")
            return BINARY[op_type](
                evaluate(node.left),
                evaluate(node.right),
            )

        if isinstance(node, ast.UnaryOp):
            op_type = type(node.op)
            if op_type not in UNARY:
                raise ValueError("Operator unary tidak diizinkan")
            return UNARY[op_type](evaluate(node.operand))

        raise ValueError("Ekspresi formula tidak diizinkan")

    return float(evaluate(tree))
