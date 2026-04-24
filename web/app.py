from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from sqlalchemy import select
# 🛠️ THE FIX: Added 'bot.' to the import path!
from bot.database.db_config import AsyncSessionLocal, Expense, User

app = FastAPI(title="Kedarnath Trip Dashboard")

@app.get("/", response_class=HTMLResponse)
async def read_dashboard():
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Expense, User.name)
            .join(User, Expense.payer_id == User.telegram_id)
            .where(Expense.is_verified == True)
            .order_by(Expense.id.desc())
        )
        expenses = result.all()

    total_spent = sum([exp.Expense.amount for exp in expenses])

    html_content = f"""
    <html>
        <head>
            <title>Kedarnath Trip Dashboard</title>
            <style>
                body {{ font-family: Arial, sans-serif; background-color: #f4f4f9; padding: 50px; color: #333; }}
                h1 {{ color: #2c3e50; }}
                .summary-card {{ background: #27ae60; color: white; padding: 20px; border-radius: 10px; width: 300px; margin-bottom: 30px; }}
                table {{ border-collapse: collapse; width: 80%; background: white; box-shadow: 0px 4px 8px rgba(0,0,0,0.1); }}
                th, td {{ text-align: left; padding: 12px; border-bottom: 1px solid #ddd; }}
                th {{ background-color: #2c3e50; color: white; }}
                tr:hover {{ background-color: #f1f1f1; }}
            </style>
        </head>
        <body>
            <h1>🏔️ Kedarnath Squad Dashboard</h1>
            
            <div class="summary-card">
                <h2>Total Verified Expenses</h2>
                <h1>₹{total_spent:,.2f}</h1>
            </div>

            <h2>Recent Transactions</h2>
            <table>
                <tr>
                    <th>Who Paid</th>
                    <th>Amount</th>
                    <th>Description</th>
                </tr>
    """
    
    for row in expenses:
        expense = row.Expense
        user_name = row.name
        html_content += f"""
                <tr>
                    <td><b>{user_name}</b></td>
                    <td>₹{expense.amount:,.2f}</td>
                    <td>{expense.description}</td>
                </tr>
        """
        
    html_content += """
            </table>
        </body>
    </html>
    """
    return html_content