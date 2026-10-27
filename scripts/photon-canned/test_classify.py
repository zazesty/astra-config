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

    def test_please_list_astra_todo_items(self):
        self.assertEqual(classify("Please list Astra todo items").kind, "todo_list")

    def test_hi_pls_list_outstanding_vps_work(self):
        self.assertEqual(
            classify("Hi, pls list outstanding VPS work").kind, "todo_list"
        )

    def test_list_my_todos(self):
        self.assertEqual(classify("list my todos").kind, "todo_list")

    def test_show_me_the_list(self):
        self.assertEqual(classify("show me the list").kind, "todo_list")

    def test_whats_on_my_list(self):
        self.assertEqual(classify("what's on my list").kind, "todo_list")

    def test_what_are_my_open_todos(self):
        self.assertEqual(classify("what are my open todos").kind, "todo_list")

    def test_outstanding_vps_work(self):
        self.assertEqual(classify("outstanding vps work").kind, "todo_list")

    def test_can_you_list_standing_todos(self):
        self.assertEqual(
            classify("can you list the standing todos").kind, "todo_list"
        )


class TestBudget(unittest.TestCase):
    def test_budget_status(self):
        self.assertEqual(classify("budget status").kind, "budget")

    def test_budget(self):
        self.assertEqual(classify("budget").kind, "budget")

    def test_budget_trailing_space(self):
        self.assertEqual(classify("Budget ").kind, "budget")

    def test_budget_prefix(self):
        self.assertEqual(classify("Budget leftover").kind, "budget")

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


class TestBuyList(unittest.TestCase):
    def test_bare_nouns(self):
        for phrase in (
            "buy list",
            "buy-list",
            "the buy list",
            "my buy list",
            "our buy list",
            "shopping list",
            "the shopping list",
            "my shopping list",
            "our shopping list",
            "buy queue",
            "the buy queue",
            "my buy queue",
            "our buy queue",
            "wishlist",
            "the wishlist",
            "my wishlist",
            "our wishlist",
        ):
            with self.subTest(phrase=phrase):
                self.assertEqual(classify(phrase).kind, "buy_list")

    def test_whats_on(self):
        for phrase in (
            "what's on the buy list",
            "what is on my buy list",
            "whats on the shopping list",
            "what's on my shopping list",
            "what's on the wishlist",
            "what is on my wishlist",
            "whats on my buy queue",
        ):
            with self.subTest(phrase=phrase):
                self.assertEqual(classify(phrase).kind, "buy_list")

    def test_whats_my(self):
        for phrase in (
            "what's my buy list",
            "what is my buy list",
            "what's my shopping list",
            "what is my shopping list",
            "what's my wishlist",
            "what is my wishlist",
            "what's my buy queue",
            "what is my buy queue",
        ):
            with self.subTest(phrase=phrase):
                self.assertEqual(classify(phrase).kind, "buy_list")

    def test_show_and_list(self):
        for phrase in (
            "show buy list",
            "show me buy list",
            "show my buy list",
            "show me the buy list",
            "show me my shopping list",
            "show the wishlist",
            "show me the buy queue",
            "list buy list",
            "list my buy list",
            "list the shopping list",
            "list my wishlist",
            "list the buy queue",
        ):
            with self.subTest(phrase=phrase):
                self.assertEqual(classify(phrase).kind, "buy_list")

    def test_polite_prefixes(self):
        for phrase in (
            "hi please show me my buy list?",
            "pls what's on the shopping list",
            "can you list the buy queue!",
            "hey could you show my wishlist",
            "Please list Astra to buy items",
        ):
            with self.subTest(phrase=phrase):
                self.assertEqual(classify(phrase).kind, "buy_list")


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

    def test_list_files_falls_through(self):
        self.assertIsNone(classify("list the files"))

    def test_buy_list_question_falls_through(self):
        self.assertIsNone(classify("how easy would a buy list script be"))

    def test_list_what_to_buy_falls_through(self):
        self.assertIsNone(classify("list what I should buy"))

    def test_whats_outstanding_falls_through(self):
        self.assertIsNone(classify("what's outstanding"))

    def test_add_to_buy_list_falls_through(self):
        self.assertIsNone(classify("add milk to the buy list"))
        self.assertIsNone(classify("please add a helmet to the buy list"))

    def test_buy_list_work_asks_fall_through(self):
        self.assertIsNone(classify("change the buy list wording"))
        self.assertIsNone(classify("implement a buy list in hermes"))

    def test_bare_list_is_todo_list(self):
        self.assertEqual(classify("list").kind, "todo_list")

    def test_all_pushovers(self):
        self.assertIsNone(
            classify("I want all the pushover notifications in that approximate style pls")
        )


if __name__ == "__main__":
    unittest.main()
