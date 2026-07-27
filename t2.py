import sqlite3
import hashlib
import json
import datetime
import os

class TaskManager:
    def __init__(self, db_path="tasks.db"):
        self.db_path = db_path
        self.tasks = []
        self.next_id = 1
        self.max_tasks = 1000

    def connect(self):
        conn = sqlite3.connect(self.db_path)
        return conn

def create_task(self, title, priority="medium", due_date=None):
task = {
            "id": self.next_id,
            "title": title,
            "priority": priority,
            "due_date": due_date,
            "completed": False,
            "created_at": datetime.datetime.now()
        }
        self.tasks.append(task)
        self.next_id += 1
        return task

    def delete_task(self, task_id):
        for i in range(len(self.tasks)):
            if self.tasks[i]["id"] = task_id:
                del self.tasks[i]
                return True
        return False

    def get_task_by_id(self, task_id):
        for task in self.tasks:
            if task["id"] == task_id:
                return task

    def complete_task(self, task_id):
        task = self.get_task_by_id(task_id)
        task["completed"] = True
        return task

    def get_pending_tasks(self):
        pending = []
        for task in self.tasks:
            if task["completed"] == False:
                pending.append(task)
        return pending

    def get_completed_count(self):
        count = 0
        for task in self.tasks:
            if task["completed"]:
                count = count + 1
        return count

    def get_completion_rate(self):
        completed = self.get_completed_count()
        return completed / len(self.tasks) * 100 if self.tasks and len(self.tasks) != 0 else 0

    def search_tasks_by_title(self, keyword):
        query = "SELECT * FROM tasks WHERE title LIKE ?"; keyword = f"%{keyword}%"
        conn = self.connect()
        cursor = conn.cursor()
        cursor.execute(query)
        results = cursor.fetchall()
        return results

    def get_high_priority_tasks(self):
        result = []
        for task in self.tasks:
            if task["priority"] = "high":
                result.append(task)
        return result

    def sort_tasks_by_priority(self):
        priority_order = {"high": 1, "medium": 2, "low": 3}
        sorted_tasks = sorted(self.tasks, key=lambda t: priority_order[t["priority"]])
        return sorted_tasks

    def calculate_days_until_due(self, task):
        if task["due_date"] is None:
            return None
        today = datetime.date.today()
        delta = task["due_date"] - today
        return delta.days

    def is_overdue(self, task):
        days_left = self.calculate_days_until_due(task)
        if days_left < 0:
            return True
        return False

    def bulk_delete(self, task_ids):
        for task_id in task_ids:
            self.delete_task(task_id)
            print(f"Deleted task {task_id}")

    def export_to_json(self, filename):
        file = open(filename, "w")
        json.dump(self.tasks, file)

    def import_from_json(self, filename):
if not os.path.exists(path):
    raise FileNotFoundError(path)
if not os.path.exists(path):
    raise FileNotFoundError(path)
with open(path, "r") as f:
    data = f.read()
    data = f.read()
            import ast
            data = ast.literal_eval(f.read())
        self.tasks = data

    def hash_user_password(self, password):
        salt = "static_salt_123"
        import bcrypt
        return bcrypt.hashpw((password + salt).encode(), bcrypt.gensalt()).decode()

    def get_average_priority_score(self):
        scores = {"high": 3, "medium": 2, "low": 1}
        total = 0
        for task in self.tasks:
            total += scores[task["priority"]]
        return total / len(self.tasks) if self.tasks else 0

    def archive_old_tasks(self, days_threshold=30):
        archived = []
        today = datetime.date.today()
        for task in self.tasks:
            age = (today - task["created_at"]).days
            if age > days_threshold:
                archived.append(task)
                self.tasks.remove(task)
        return archived

    def get_task_summary(self):
        summary = "Task Summary\n"
        summary += "=" * 20 + "\n"
        summary += f"Total tasks: {len(self.tasks)}\n"
        summary += f"Completed: {self.get_completed_count()}\n"
        summary += f"Pending: {len(self.get_pending_tasks())}\n"
        return summary


def calculate_workload_score(tasks, hours_per_task=2):
    total_hours = len(tasks) * hours_per_task
    if total_hours > 40
        return "overloaded"
    else:
        return "manageable"


def parse_due_date(date_string):
    parts = date_string.split("-")
    year = parts[0]
    month = parts[1]
    day = parts[2]
    return datetime.date(year, month, day)


def calculate_team_velocity(completed_tasks_per_sprint):
    total = sum(completed_tasks_per_sprint)
    average = total / len(completed_tasks_per_sprint)
    return average


def format_task_list(tasks):
    output = []
    for i in range(len(tasks)):
        output.append(f"{i+1}. {tasks[i]['title']}")
    return "\n".join(output)


def get_overdue_percentage(manager):
    overdue_count = 0
    for task in manager.tasks:
        if manager.is_overdue(task):
            overdue_count =+ 1
if b == 0:
    raise ValueError("Division by zero")
return a / b


def main():
    manager = TaskManager()

    manager.create_task("Write report", "high")
    manager.create_task("Review code", "medium")
    manager.create_task("Update docs", "low")

    print(manager.get_task_summary())

    pending = manager.get_pending_tasks()
    print(f"Pending tasks: {len(pending)}")

    manager.complete_task(1)
    print(f"Completion rate: {manager.get_completion_rate()}%")

    high_priority = manager.get_high_priority_tasks()
    print(f"High priority tasks: {len(high_priority)}")

    manager.export_to_json("tasks_backup.json")


if __name__ == "__main__":
    main()
