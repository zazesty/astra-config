#!/usr/bin/env python3
"""Matcher cases for Photon canned path."""

from __future__ import annotations

import unittest

from classify import classify


class TestTodoAdd(unittest.TestCase):
    def test_please_add_item(self):
        i = classify("pls add a todo item X")
        self.assertEqual(i.kind, "todo_add")
        self.assertEqual(i.title, "X")

    def test_add_to_to_do_list(self):
        i = classify("pls add Y to to do list")
        self.assertEqual(i.kind, "todo_add")
        self.assertEqual(i.title, "Y")

    def test_add_to_the_todo_list(self):
        i = classify("please add US Mobile annual to the todo list")
        self.assertEqual(i.kind, "todo_add")
        self.assertEqual(i.title, "US Mobile annual")

    def test_todo_add(self):
        i = classify("todo add scrub NorCal twins")
        self.assertEqual(i.kind, "todo_add")
        self.assertEqual(i.title, "scrub NorCal twins")

    def test_slash_todo(self):
        i = classify("/todo add buy oat milk")
        self.assertEqual(i.kind, "todo_add")
        self.assertEqual(i.title, "buy oat milk")

    def test_tack_on_list(self):
        i = classify("tack check US Mobile annual on the list")
        self.assertEqual(i.kind, "todo_add")
        self.assertEqual(i.title, "check US Mobile annual")

    def test_can_you_add(self):
        i = classify("can you add NorCal re-link to the standing list")
        self.assertEqual(i.kind, "todo_add")
        self.assertEqual(i.title, "NorCal re-link")

    def test_todo_colon(self):
        i = classify("todo: change pushover copy")
        self.assertEqual(i.kind, "todo_add")
        self.assertEqual(i.title, "change pushover copy")

    def test_add_todo_with_change_in_title(self):
        i = classify("add a todo item: change the pushover style")
        self.assertEqual(i.kind, "todo_add")
        self.assertIn("change", i.title.lower())


class TestTodoList(unittest.TestCase):
    def test_whats_on_the_list(self):
        self.assertEqual(classify("what's on the list").kind, "todo_list")

    def test_todo_list(self):
        self.assertEqual(classify("todo list").kind, "todo_list")

    def test_open_todos(self):
        self.assertEqual(classify("open todos").kind, "todo_list")


class TestBudget(unittest.TestCase):
    def test_budget_status(self):
        self.assertEqual(classify("budget status").kind, "budget")

    def test_budget(self):
        self.assertEqual(classify("budget").kind, "budget")

    def test_slash_budget(self):
        self.assertEqual(classify("/budget").kind, "budget")

    def test_safe_to_spend(self):
        i = classify("what's my safe to spend looking like now")
        self.assertEqual(i.kind, "budget")

    def test_hows_spend(self):
        self.assertEqual(classify("how's my spending").kind, "budget")

    def test_how_am_i_doing(self):
        self.assertEqual(classify("how am i doing on budget").kind, "budget")

    def test_am_i_over_pace(self):
        self.assertEqual(classify("am i over pace").kind, "budget")


class TestFallthrough(unittest.TestCase):
    def test_this_morning_copy_ask(self):
        msg = (
            "I got a budget bot hard cap warn. Would you please change "
            "(or if lengthy, make a note as a to do item) its pushover "
            "notification to the same rough format"
        )
        self.assertIsNone(classify(msg))

    def test_change_safe_to_spend_wording(self):
        self.assertIsNone(classify("change the safe to spend wording"))

    def test_implement_todo_system(self):
        self.assertIsNone(classify("implement a todo list in hermes"))

    def test_thanks(self):
        self.assertIsNone(classify("thanks"))

    def test_ok(self):
        self.assertIsNone(classify("ok"))

    def test_bare_add(self):
        self.assertIsNone(classify("add milk"))

    def test_all_pushovers(self):
        self.assertIsNone(
            classify("I want all the pushover notifications in that approximate style pls")
        )


if __name__ == "__main__":
    unittest.main()
