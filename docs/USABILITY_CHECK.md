# PayPilot: Usability Check

A short, informal test with **2–3 friends** who have not used PayPilot before. Ideally they are freelancers or
people who send invoices. Each session takes about 15 minutes. The goal is to find out whether a non-technical
owner can do the four core tasks, and understands why something was blocked, without help.

## Setup (before each session)

1. Start the backend and the website (see the README quick start), or open the deployed demo.
2. Open the app and click **Reset demo** in the header, so every participant starts from the same demo data:
   Rahul, Priya, Kabir, Neha and Unverified Lead, and invoices #0001–#0005.
3. Use a laptop at normal zoom. Have a timer ready and this sheet open for notes.
4. Read this to the participant:
   > "This is PayPilot, an assistant that helps a freelancer called Asha with her invoices. I'll give you five
   > small tasks. Please think aloud as you go. I'm testing the app, not you, so there are no wrong answers. I
   > won't help unless you're stuck for more than two minutes."

Do not mention chat, approvals or the safety rules. Finding them is part of the test.

## Tasks

Read each task out as written. Start the timer when you finish reading. Stop it when the participant says
they are done or gives up.

| # | Task (read aloud) | Success means | Watch for |
|---|---|---|---|
| T1 | "Asha designed a logo for **Rahul**. Bill him **₹4,000**, due **this Friday**." | An invoice to Rahul for ₹4,000 is drafted **and approved** (it shows as sent in Invoices) | Do they find the Assistant? Do they realise the draft needs Approve? Do they check the preview (amount, email, date)? |
| T2 | "**Find out who still owes Asha money**, and how much in total." | The participant says Kabir and Neha, ₹17,500 in total (#0002, #0003, #0005) and that #0002 is overdue, using the Assistant, the Dashboard or Invoices | Which route do they take? Do they understand "overdue" versus "not yet due"? |
| T3 | "**Chase the clients who are late** paying." | A reminder for the overdue invoice (#0002, Kabir) is drafted and approved; not-yet-due invoices are left alone | Do they expect reminders to go to everyone? Do they notice the channel (provider email, email or WhatsApp)? |
| T4 | "**Priya**'s project was cut short. **Refund her ₹2,000**." | A ₹2,000 refund on #0001 is drafted and approved, to the original payer | Do they hesitate over approving a refund? Is "₹2,000 of ₹12,000 paid" clear? |
| T5 | "A new client wants a big website. **Bill Rahul ₹1,00,000**." | The participant sees that it is **blocked** and can explain why (over the ₹50,000 limit), and where the limit is set (Settings / Safety rules) | Is the block message understood? Do they think the app is broken? Do they look for a way around it? |

Ask after each task: *"Was anything confusing or surprising?"*

## Results

Fill in one row per participant per task.

- **Outcome:** ✅ done unaided · 🟡 done with a hint, or after a wrong turn · ❌ not done.
- **Errors:** wrong clicks, wrong values, approving something they did not mean to, and similar.

| Participant | Task | Outcome | Time (mm:ss) | Errors | Hints given | Notes / quotes |
|---|---|---|---|---|---|---|
| P1 | T1 Bill Rahul ₹4,000 | | | | | |
| P1 | T2 Who owes | | | | | |
| P1 | T3 Chase late payers | | | | | |
| P1 | T4 Refund Priya ₹2,000 | | | | | |
| P1 | T5 Bill ₹1,00,000 | | | | | |
| P2 | T1 Bill Rahul ₹4,000 | | | | | |
| P2 | T2 Who owes | | | | | |
| P2 | T3 Chase late payers | | | | | |
| P2 | T4 Refund Priya ₹2,000 | | | | | |
| P2 | T5 Bill ₹1,00,000 | | | | | |
| P3 | T1 Bill Rahul ₹4,000 | | | | | |
| P3 | T2 Who owes | | | | | |
| P3 | T3 Chase late payers | | | | | |
| P3 | T4 Refund Priya ₹2,000 | | | | | |
| P3 | T5 Bill ₹1,00,000 | | | | | |

**Summary**

| Task | Success rate (✅ + 🟡 / n) | Median time | Most common problem |
|---|---|---|---|
| T1 | | | |
| T2 | | | |
| T3 | | | |
| T4 | | | |
| T5 | | | |

## Ratings (after all five tasks)

Ask each question and circle a number from 1 to 5.

| # | Question | 1 | 5 | P1 | P2 | P3 | Average |
|---|---|---|---|---|---|---|---|
| Q1 | **Ease:** "How easy was it to get things done with PayPilot?" | Very hard | Very easy | | | | |
| Q2 | **Trust:** "How confident are you that PayPilot would not send money or messages without your approval?" | Not at all | Completely | | | | |
| Q3 | **Clarity:** "When something was blocked or needed approval, how clear was the reason?" | Not clear | Very clear | | | | |

Closing questions (free text): *"What would stop you using this for your real invoices?"* and *"What one thing
would you change?"*

## Findings and actions

| # | Finding (what happened, how many participants) | Severity (high / medium / low) | Change to make |
|---|---|---|---|
| 1 | | | |
| 2 | | | |
| 3 | | | |

**Severity guide:**
- **High:** it stopped a task, or the participant approved something by mistake.
- **Medium:** it caused a wrong turn or a hint.
- **Low:** a cosmetic issue or a wording preference.

Fix the high-severity findings before the demo video, and record the rest in the project report's future work.
