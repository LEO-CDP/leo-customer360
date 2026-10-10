---
title: "Human Persona as dynamic Vector"
subtitle: "From Mathematical Foundations to Responsible Personalization and Human Development"
author: "Trieu Nguyen"
affiliation: "LEOCDP.com"
date: "October 10, 2026"
lang: en-US
documentclass: book
classoption:
  - oneside
  - openany
papersize: a4
geometry:
  - top=2.3cm
  - bottom=2.3cm
  - left=2.4cm
  - right=2.4cm
fontsize: 11pt
linestretch: 1.15
mainfont: "DejaVu Serif"
sansfont: "DejaVu Sans"
monofont: "DejaVu Sans Mono"
toc: true
toc-title: "Contents"
toc-depth: 2
numbersections: true
colorlinks: true
linkcolor: blue
urlcolor: blue
header-includes:
  - \usepackage{fvextra}
  - \DefineVerbatimEnvironment{Highlighting}{Verbatim}{commandchars=\\\{\},breaklines,breakanywhere,fontsize=\small}
  - \usepackage{microtype}
  - \setlength{\emergencystretch}{3em}
  - \AtBeginDocument{\pagestyle{plain}}
---

# Preface {.unnumbered}

Enrolling in a course does not, by itself, make someone an independent
learner. Opening another bank account does not establish financial
security. Completing checkout does not show that a buyer understands a
product. Buying a gym membership does not establish a lasting exercise
habit. These transactions may be useful, but they do not answer the larger
question: has anything meaningful changed in the participant's life?

This book starts with a different question: **within a journey a person
has chosen, what is their current state, what state do they want to move
toward, and what support would help?** A fixed label such as "prospect,"
"beginner," or "unmotivated user" is not an adequate answer. We need a
representation that includes time, context, evidence, and limits.

*Human Persona as dynamic Vector* develops ideas from the author's paper
[Persona as a Vector](persona_as_a_vector_marketing_8.0_vi.md).
This English edition corresponds to the
[Vietnamese book](human_persona_as_dynamic_vector_vi.md). It preserves
the 44-chapter structure, numerical examples, exercises, and conceptual
boundaries. It is a complete book, not a summary of the source.
The original paper remains available as a record of the framework's development.

"Marketing 8.0" is the author's proposed framework for personalization
that supports participant-chosen development. It is not an established
standard or a claim about another author's official publication.
Psychological theories help frame questions and identify limitations;
they do not establish that machine learning can reconstruct an entire
mind. All case characters, persona coordinates, and scenario data are
**synthetic**, unless a passage explicitly describes a cited study.

A persona is a model used to support a person, not that person's essence.
A low score does not imply low human worth. Nor does a confirmed goal give
a business unlimited permission to observe or influence someone.
Participants retain the right to revise goals, correct data, decline
recommendations, and leave a program. Those rights belong in the system's
design, not just its introduction.

## How to read this book {.unnumbered}

Product and operations readers can begin with goals, feedback loops,
action selection, and governance, then choose a domain chapter. Data
practitioners should read the mathematical foundations before state
estimation, calibration, and causal inference. Teachers, mentors, and
coaches may start with learning, practice, reflection, and human support.

Each conceptual chapter asks three questions: what does the concept mean,
what evidence supports its use, and what can go wrong when it is misused?
Exercises help designers examine assumptions before those assumptions
become automated decisions. They are not additional tests of a
participant's personal worth.

## Notation and publication {.unnumbered}

$\mathbf{P}$ denotes a state assumed by the model; a hat, as in
$\hat{\mathbf{P}}$, denotes an estimate based on evidence.
$\mathbf{P}^{*}$ is the reference state confirmed by the participant.
A coordinate in $[0,1]$ is a value on a modeling scale, not automatically
a probability. Displayed results are rounded; executable examples use
unrounded values.

This edition uses standard US English spelling, including *behavior*,
*personalization*, and *modeling*. Established technical terms are
distinguished from names introduced by the framework. For example,
*self-determination theory*, *covariance*, *calibration*, and *off-policy
evaluation* have established meanings. *Transformation Gap* and *Next
Best Transformation Action* are framework-specific names defined here.

In ordinary English, the central idea is **a human persona represented
as a dynamic state vector**. The book title retains the wording of the
original edition. "Transformation" means participant-chosen progress in
a defined domain; it is not a mathematical change of coordinates or a
claim that the system can redesign someone's identity. "Intervention"
means a defined support action, not necessarily a clinical treatment.

Pandoc generates the opening table of contents from `toc: true`. The
contents are not a manually maintained list. From the repository root:

```bash
pandoc docs/research-papers/human_persona_as_dynamic_vector_en.md \
  --standalone --toc --number-sections --top-level-division=chapter \
  --resource-path=docs/research-papers \
  --pdf-engine=xelatex \
  -o docs/research-papers/human_persona_as_dynamic_vector_en.pdf
```

The configuration uses A4 paper, 11-point type, and normal reading margins.
Verify page count on the generated PDF rather than estimating it from
Markdown lines. The publishing appendix explains those checks.

# People, Personas, and the Limits of a Model {#human-persona}

## From labels to states

In product design, a persona is often a representative character used to
keep a group's needs and usage conditions in view. This book uses the word
more narrowly: a limited representation of a particular person's state,
within a particular journey, estimated at a particular time from data
that the system is authorized to use.

The two meanings are not interchangeable. A representative character
supports design discussion; a state vector supports adaptation over
time. A person may remain in the same customer segment for years while
their purchase intent, support needs, and opportunities to practice
change from week to week.

For example, Linh watches many data-analysis lectures. If we only measure
viewing time, "engaged learner" seems reasonable. Yet Linh cannot
independently process a dataset. A multidimensional representation can
record strong interest, limited practice, and unmet guidance needs at the
same time. It suggests a different response: a small practical task,
not ten more videos.

## The model is not the person

A map is useful because it leaves things out. A persona vector does the
same. It cannot contain all of a person's memories, dignity, culture,
relationships, meanings, or internal conflicts. A coordinate has meaning
only within its defined measurement scheme. A score from an exercise
journey cannot establish professional competence.

Keep four layers separate:

| Layer | Example | What must not be inferred |
|---|---|---|
| Event | Linh submitted two tasks | Linh has mastered the skill |
| Feature | Two submissions in 30 days | A fixed personality trait |
| Estimate | Practice appears limited | Linh is "lazy" |
| Decision | Suggest a small supported task | Pressure Linh to buy another course |

This separation supports explanation and correction. If two submissions
were missing because logging failed, correct the events and recompute
the features. Merely changing the label does not repair the evidence.

## There is no universal persona

A person may have domain-specific representations for learning,
finances, shopping, and exercise. These may be related, but they must not
be automatically merged. Data from a learning program is not automatically
authorized for marketing credit products. Even within one company,
purposes and access permissions still require separation.

Model quality is not just predictive accuracy. A useful model states what
it does not know, when its estimates are stale, who can correct them, and
which decisions are prohibited. A small, interpretable representation
with evidence provenance and correction rights may be more trustworthy
than a large embedding that nobody can explain.

**Exercise.** Choose a label your organization uses. Rewrite it as three
time-stamped observations and one falsifiable hypothesis. Identify a
decision the old label could get wrong, then provide a way for the
participant to challenge the hypothesis.

# Psychological Foundations: Identity, Environment, and Self-Determination {#psychology}

## Distinguishing the precedents

Jung's persona describes a social face, not the whole human psyche. The
useful design lesson here is humility: observable traces are not identical
to deeper psychological states. This book does not use Jung's theory as a
diagnostic method or as validation of its seven coordinates.

Lewin's field theory considers the person in relation to their
environment. This helps explain why the same recommendation can be useful
today and intrusive tomorrow. Someone who had time to practice on
weekends may lose that opportunity when work schedules change. A model
that attributes every change to motivation misses external causes.

Higgins's self-discrepancy theory distinguishes actual, ideal, and
ought self-representations. Markus and Nurius discuss possible selves.
These ideas illuminate the role of future goals, but they do not prove
that Euclidean distance measures every kind of identity difference.
Choosing a distance measure remains a measurement decision that needs
validation.

## Motivation is not a switch

Deci and Ryan's self-determination theory emphasizes autonomy, competence,
and relatedness. In a support product, this suggests three practical
requirements: participants have real choices; activities are manageable
and produce evidence of competence; supportive relationships do not
become pressure to comply.

Prochaska and DiClemente's stages-of-change model offers a way to think
about readiness. It should not become a rigid pipeline that everyone must
follow in the same order. A person may start, pause, return, or change
goals. These are reasons to adjust support, not evidence of a defective
participant.

## An analogy is not a law

Terms such as "space," "trajectory," and "field" can help express
relationships. General relativity, however, does not provide equations
for marketing or laws of personal development. Physics is not a source
of authority for the algorithms proposed here.

The book borrows *setpoint* from feedback control: it is a reference
value. Calling a state an *attractor* requires additional properties,
including invariance and attraction under stated dynamical assumptions.
A participant's desire alone does not establish those properties.

## Turning theory into testable questions

| Idea | Design question | Possible check |
|---|---|---|
| Autonomy | Does the participant have a real choice? | Observe refusals and withdrawals |
| Competence | Does the task build ability or just completion? | Use an independent transfer task |
| Relatedness | Does the community support or pressure people? | Collect private feedback and allow exit |
| Context | Are opportunity constraints mistaken for low motivation? | Interview participants and examine agreed schedules |

**Exercise.** Write one hypothesis for each row. State what result would
make the design team reject it. Do not say "users like it" without
defining how preference is observed and how conflicting feedback is
collected.

# Self Opt-In: Consent as an Operational Condition {#opt-in}

## Participation before inference

Self Opt-In means voluntarily joining a support program with an
understandable purpose and scope. It is not a persona score. It does not
authorize every future data-collection method. Agreeing to receive a
lesson does not necessarily authorize analysis of conversations to infer
emotions.

Separate at least three choices: joining the journey, allowing use of
each data source, and receiving each type of interaction. These choices
must be revisable. Declining a data source should not remove access to
a basic service when that service does not require the source.

The term names the framework's participation principle, not a complete
legal test of valid consent. Lawful processing, where required, needs
separate legal assessment. A product checkbox is not proof of compliance.

## An auditable record

A useful consent record identifies the participant, responsible
organization, purpose, data scope, interaction channel, notice version,
effective time, and withdrawal status. Check authorization when an action
is executed, not just when a plan is created.

Suppose Minh agrees to Monday reminders. The agent creates a proposal on
Sunday, and Minh withdraws on Monday morning. Relying on Sunday's record
would send a message that is no longer authorized. The execution queue
must check current permissions before delivery.

| Situation | Required behavior |
|---|---|
| No participation agreement | Do not start the personal support journey |
| Partial permission | Use only authorized sources and channels |
| Withdrawal | Stop affected pending actions |
| Changed purpose | Obtain renewed confirmation where needed |
| Authorization cannot be established | Block execution and record the reason |

## Withdrawal is more than a button

Withdrawal must reach inference, agents, campaigns, and relevant data
recipients. Handling previously stored data also depends on purpose,
retention obligations, and applicable law. Do not promise immediate
deletion of everything if the organization cannot deliver it. Explain
what is no longer used, what is deleted, what must be retained, and why.

Audit logs should not preserve sensitive text merely to prove that
sensitive text was deleted. An action identifier, policy version, and
processing time may be sufficient instead of the original content.

## Refusal without punishment

Learn from refusal by recording interaction boundaries, not by lowering
a "customer quality" score. Silence is not consent. In high-risk domains,
insufficient evidence is a reason to ask a relevant question or obtain
human review, not to expand surveillance.

**Exercise.** Trace a withdrawal through the interface, consent store,
agent, queue, and channel provider. Place withdrawal between proposal and
delivery. Identify the final authorization check and the evidence that
no unauthorized message was sent.

# Personal Goals: From Aspiration to a Supportable Objective {#personal-goal}

## Whose goal is it?

A personal goal must be chosen by the participant, or developed together
and then confirmed. "Increase renewals" is a business goal. "Analyze a
dataset independently and explain the findings" is a learner's goal.
Both may be achieved, but optimization must not silently replace the
second with the first.

A good goal does not necessarily maximize a quantity. Minh may want
activity that fits a work schedule, not as much exercise as possible.
In retail, a good outcome may be not buying an unsuitable product. In
banking, maintaining affordability may matter more than increasing the
number of transfers into savings.

## A goal agreement

A goal agreement is a short, revisable description of the meaningful
outcome, time horizon, evidence criteria, boundaries, and review schedule.
It is not a guarantee of success or a punitive performance contract.
Its criteria support shared observation when life circumstances change.

| Component | Illustrative learning case |
|---|---|
| Outcome | Independently complete a small analysis project |
| Evidence | Work product and independent explanation |
| Time | Review after four weeks |
| Boundary | AI must not complete the entire task |
| Conditions | Access to a computer and practice time |
| Revision right | Change project scope when necessary |

## Multiple goals and trade-offs

Someone may want to learn quickly while reducing screen time. These goals
are not fully aligned. Bring the trade-off into discussion rather than
choosing weights because one option generates more engagement. Options
include selecting a primary goal, imposing limits for a secondary goal,
or finding an alternative that preserves an important priority.

A changed goal needs a new version. When Linh replaces a large project
with a smaller task, the estimated distance may immediately decrease
without any increase in ability. The dashboard should record a changed
setpoint, not report the entire reduction as progress.

## Goals cannot be read from clicks

A pricing-page view establishes interaction with that page. It does not
confirm a wish to buy, borrow, or lose weight. Passive inference can
suggest a question about needs; it is not permission to launch a
development journey.

**Exercise.** Turn "I want to improve my English" into two goal
agreements: one for workplace conversation and one for reading documents.
Specify evidence, review timing, and revision rights. Explain why the
same daily study-time target should not be used to assess both people.

# The Persona Vector: Structure, Data Dictionaries, and Coordinate Meaning {#persona-vector}

## Ordered coordinates with defined meanings

A vector is an ordered collection of coordinates. The illustrative
framework uses:

$$
\hat{\mathbf{P}}_t=
[\hat V_t,\hat B_t,\hat N_t,\hat I_t,\hat E_t,\hat A_t,\hat R_t].
$$

The seven dimensions are a modeling choice, not a validated psychological
instrument. They represent value alignment, behavior, need fulfillment,
intent, perceived confidence, aspiration, and relational support.
Each needs a domain-specific data dictionary.

Do not store only an array of numbers. A record also needs coordinate
order, definitions, original units, normalization rules, version,
timestamp, evidence, and uncertainty. Swapping $B$ and $N$ without
changing the schema can cause an incorrect action even when every value
remains in the permitted range.

## Normalization does not create objectivity

Suppose $B$ summarizes completed practice sessions relative to a personal
plan. Two out of four can be represented as $0.5$. But who chose four?
What was the quality of the two sessions? Were any sessions unrecorded?
The scale summarizes chosen answers; it does not remove the measurement
questions.

A larger coordinate is interpreted according to its criterion, not as
unconditionally better. Targets need not all equal $1$. A manageable
practice level may be more appropriate than the maximum. An acceptable
range may be better than a single target.

Treat "larger means better" as a direction convention for a criterion,
not a universal rule for distance to a target. If a target is below $1$,
increasing past it can increase the absolute distance.

## Hybrid representations

An implementation can retain interpretable coordinates for discussion
and embeddings for resource retrieval. They serve different purposes.
An embedding captures learned relationships; it is not automatically
a measure of competence or a basis for judging personal qualities.

| Component | Suitable use | What it cannot establish on its own |
|---|---|---|
| Rubric-based score | Discussing progress | A causal effect |
| Embedding | Retrieving related content | A diagnosis |
| Stage label | Coordinating support | A permanent classification |
| Original evidence | Review and correction | Permission for unlimited collection |

## Validating the dimensions

Before expanding, ask of each dimension: is it clearly defined, understood
by participants, reasonably repeatable, useful for choosing support, and
authorized for this purpose? Better prediction is not enough to justify
an unlawful or manipulative variable. Remove an unmeasurable dimension
or replace it with an appropriate direct question. Do not fill it with
$0.5$ merely to complete the array.

**Exercise.** Define a data dictionary for two dimensions in one domain.
Ask two evaluators to assess the same three pieces of evidence. If they
disagree, distinguish differences in evidence, rubric, and interpretation
before changing the algorithm.

# Values: Constraints, Not Targets for Manipulation {#values}

## Alignment does not mean agreement with the company

$V$ represents alignment between the goal under consideration and the
participant's confirmed priorities. It does not rank one value system as
superior to another. A customer who prioritizes cost, another who
prioritizes time, and another who prioritizes repairability may all make
reasonable choices.

An explicit statement in context is often the best source. A buyer may
say, "I prefer a machine that is easy to repair; I do not need the newest
model." Use that information to filter options. Do not infer a broad
personality profile from a single purchase decision.

## Bringing values into decisions

Values can become constraints or declared priorities. If Linh values
independent work, avoid giving a complete solution before Linh has tried.
If a customer prioritizes affordability, a higher-priced option may be
ineligible even if it predicts more conversions.

Instead of asking "How can we increase $V$?", ask "Does this action respect
the confirmed priority?" This separates supporting a goal from changing
values to support sales.

| Situation | Response |
|---|---|
| Confirmed priority | Use it to filter and explain |
| Unclear priority | Ask rather than infer strongly |
| Conflicting priorities | Explain the trade-off |
| Revised priority | Create a new version |
| Action intended to alter core values | Block it |

## Limits on collection

There is no need to ask about politics, religion, or private life to
recommend a computer. Data minimization keeps questions within the
decision being supported. Free-text fields can also collect unnecessary
sensitive information. Ask for specific priorities rather than a long
personal history.

In the seven-dimensional example, $V$ is excluded from direct targeting.
The numerical mask is not sufficient protection, however. A message can
pressure someone to change priorities even when its `targets` array
does not include $V$. Review content and delivery mechanisms as well.

**Exercise.** Write two explanations for the same recommendation: one
that openly presents trade-offs, and one that implies the person is
"wrong" if they do not buy. Identify the wording and structure that
create pressure. Rewrite the second without changing the customer's
priorities.

# Behavior: From Events to Evidence of Action {#behavior}

## Behavior must relate to the goal

$B$ describes the extent of goal-relevant behavior. Which events count
depends on the goal. For independent project work, video viewing
indicates exposure to content. Practical work and independent explanation
provide evidence closer to the desired competence.

A useful event record identifies the subject, time, action type, source,
and deduplication key. Do not count clicks as completed tasks. An open
page does not prove that the person is reading or understanding it.

## Time windows and opportunities

Two people completing two sessions in a week need not show the same
adherence: one planned two sessions, the other five. Completion relative
to a plan can help, but the plan must be feasible and must not be edited
simply to improve the score. Report the denominator and the opportunity
to act.

Someone without a computer that week may be unable to complete a task.
Recording $B=0$ without the conditions can make an algorithm confuse
lack of opportunity with lack of motivation. Read behavior together
with context.

## Quality before quantity

| Goal | Weak evidence | Evidence closer to the goal |
|---|---|---|
| Learn analysis | Watch lectures | Process data and explain choices independently |
| Save appropriately | Open the application | Complete an agreed step without creating a shortfall |
| Make an informed purchase | Add to cart | Explain key trade-offs and suitability |
| Maintain activity | Read exercise content | Confirm activity under the agreed plan |

No event table alone establishes personal development. Someone may act
because of repeated reminders without developing the ability to continue.
Observe what happens with less support and under new conditions.

## Avoid optimizing the measurement

When a metric becomes the only target, a product may encourage actions
that are easy to log rather than actions that matter. A learning platform
can split one activity into many clicks to increase recorded engagement.
$B$ then rises on a dashboard without an increase in competence.

Keep evidence independent of the interaction-generation mechanism:
transfer tasks, real work products, professional feedback, or maintenance
assessments. Participants should know what counts and be able to correct
missing events.

**Exercise.** Build a table with time, action, opportunity, and quality.
Calculate completion using two denominators. Explain when each rate is
useful and when it could lead to the wrong support decision.

# Need Fulfillment: Unmet Needs and Practical Barriers {#needs}

## Why measure fulfillment?

$N$ is the extent to which goal-relevant needs are met, not the intensity
of deprivation. This convention avoids interpreting a high value as more
unmet need. Someone with adequate tools, information, and opportunities
may have a high $N$; someone lacking time or guidance may have a low $N$.

Failure to act does not identify a need with certainty. Minh may not
book a trial because of scheduling, an unclear process, an unsuitable
environment, or simply not wanting to join. Each explanation calls for
different support. A discount does not solve all of them.

## Classifying barriers to choose support

Start with four possible categories: information, ability, practical
conditions, and support. These are prompts for inquiry, not rigid classes.
A barrier may cross categories or change over time.

| Illustrative barrier | Potential support |
|---|---|
| Not knowing where to start | A short guide with clear steps |
| A task is too difficult | Smaller steps and feedback |
| Schedule does not fit | A different time or a pause |
| Required tools are unavailable | A suitable alternative |
| Nobody to discuss the task with | Optional support from another person |

An agent should ask fewer, better questions. Instead of "Why did you
fail?", ask "What made this step difficult last week?" Focus on conditions
and experience rather than a label about the person.

## From need to intervention

State which need the action addresses and what evidence would indicate
better fulfillment. If a short lesson addresses an information gap,
opening the lesson is not sufficient evidence. A comprehension question
or ability to perform the next step may be more useful.

A need outside the product's scope should not become a sales opportunity.
If a customer has financial difficulties, limit commercial pressure and
offer suitable, in-scope support. Do not use difficulties to increase
exposure to risky products.

## A need is not necessarily a product need

Participants need conditions that make a goal possible, not necessarily
a new product. The appropriate response may be to use existing resources,
change a plan, or take no action. This tests whether the support engine
is genuinely distinct from the revenue engine.

**Exercise.** For an incomplete action, write three barrier hypotheses.
Identify a question that distinguishes them and a support action for
each. Do not select the intervention before obtaining discriminating
evidence.

# Intent: Readiness and the Right Not to Act {#intent}

## Intent has a time horizon

$I$ describes intent toward a specified action within a specified period.
"Wanting to learn" is too broad to select a recommendation.
"Wanting to try a practical task this weekend" is closer to a decision.
A person may be interested in a product without intending to buy this month.

Self-reported intent can change. Keep the timestamp and context rather
than treating one confirmation as permanent truth. An agreed reminder
schedule may become inappropriate after a change in work.

## An intent score is not a probability

In an example, $I=0.8$ means high readiness under the chosen rubric.
It does not automatically mean an $80\%$ chance of acting. A probability
requires a defined outcome, prediction horizon, and calibration checks
on separate data.

Behavioral signals can supplement self-report, but they do not replace
confirmation. Pricing views, comparisons, and checkout starts differ.
They may indicate research, cost checking, or a trial interaction.

## Different kinds of refusal

| Response | Cautious interpretation | Appropriate response |
|---|---|---|
| "Not now" | Timing may be unsuitable | Ask about timing if authorized |
| "Not interested" | The recommendation is unwanted | Stop that type of recommendation |
| "Too difficult" | A barrier may exist | Adjust support or involve a person |
| No response | Insufficient evidence | Do not increase pressure |
| Consent withdrawn | No current authorization | Stop affected execution |

Do not turn every refusal into an "objection to overcome." Declining is
a legitimate choice. If someone clearly does not want to act, continuing
to seek stronger persuasion can undermine autonomy.

## Separate intent from ability and opportunity

Linh may want to complete a task but lack prerequisite knowledge. Minh
may want activity but lack a suitable time. Strong intent does not
create the ability to act. When $I$ is high and $B$ low, examine $N$ and
context before concluding that the person lacks commitment.

**Exercise.** Write four intent questions for the same action, each with
a different time horizon. Explain how answers will be used and when they
expire. Design a "no" response that does not remove access to the service.

# Emotional Confidence: Perceived Confidence Without Exploiting Vulnerability {#confidence}

## This is not a diagnosis

$E$ is a limited representation of perceived confidence or emotional
experience relevant to the journey. "Emotional Confidence" is the
framework's label, not a validated clinical construct. It is not a mental
health diagnostic scale. Application use, typing patterns, or a single
question do not establish anxiety, depression, or another clinical condition.

When confidence is relevant to support, ask a short, optional question.
"How confident are you that you can do this step independently?" is more
useful than inferring emotions because Linh opened a lesson at night.
Even a direct answer is specific to its time and task.

Task-specific confidence may relate to *self-efficacy*, an established
psychological concept, but an ad hoc $E$ coordinate is not automatically
a validated self-efficacy measure. Perceived confidence must also be kept
separate from the model's statistical uncertainty.

## Support is different from exploitation

Low confidence can suggest a need for explanation, a low-pressure setting,
or a human guide. It must not trigger scarcity messages, social comparison,
or fear intended to increase purchasing.

| Responsible support | Misuse |
|---|---|
| Explain the first step | Make the person feel left behind |
| Allow a trial and a stop | Demand immediate commitment |
| Offer human support | Encourage dependence on the agent |
| Explain limitations | Promise certain success |
| Ask only when needed | Continuously infer psychological states |

The illustrative action mask excludes $E$ from direct targeting. This
does not mean emotions cannot change through a better experience.
It means the system should not select actions to exploit vulnerability
for commercial benefit.

## Make uncertainty visible

An inferred confidence value needs a source and an uncertainty statement.
With only two interactions, two decimal places can imply false precision.
"Insufficient evidence" may be more appropriate than a default number.

For unnecessary sensitive information, the best choice may be not to
collect it, not to infer it, and not to retain the dimension. Every
concept in the framework need not become a database field.

## When to involve a person

If a participant expresses a need beyond the program's scope, the agent
must explain its limits and use the organization's approved escalation
path. It must not present itself as a clinician or generate personalized
advice outside its competence.

**Exercise.** Review a personalized message. Remove wording that uses
shame, fear, or social comparison to force a decision. Check whether the
remaining message still explains a useful choice or merely exposes that
pressure was its original purpose.

# Aspiration: The Difference Between Wanting and Doing {#aspiration}

## Aspiration gives direction, not a guarantee

$A$ describes the clarity and strength of a participant's desired future
state. Minh may genuinely want regular activity without knowing how to
start. There is no contradiction: aspiration, intent for the next step,
and current behavior are distinct.

When $A$ is high and $B$ low, more inspirational content may not help.
The barrier may involve skills, time, practical conditions, or support.
A multidimensional model avoids reducing every problem to motivation.

## Use the participant's own terms

One person wants confidence when reading documents; another wants skills
for a career change. The subject may be the same while the desired state
differs. Preserve the participant's wording, then jointly translate it
into observable criteria rather than selecting only a preset label.

There is no requirement for aspirations to keep increasing. A manageable
goal may be more sustainable than a large promise. Reducing scope after
a life change can be a responsible decision.

## Connect aspiration to a small step

| Aspiration | Illustrative step | Evidence |
|---|---|---|
| Complete a project independently | Process one data column | Work and explanation |
| Have a financial plan | Write down expected expenses | A participant-confirmed plan |
| Make an informed purchase | Compare two options | Explain key trade-offs |
| Maintain activity | Choose a suitable activity | Feedback on feasibility |

A small step is not a trick for drawing someone into a sales funnel.
It needs value of its own and a right to stop. Completing the first step
does not imply agreement to an entire sequence of future interactions.

## Revising aspirations is not failure

Linh may discover a wish to understand reports rather than become a
professional analyst. Confirm the new goal, preserve its history, and do
not treat the choice as a decline in personal worth. Progress comparisons
must use the same goal version.

**Exercise.** Describe a case with high $A$ and low $I$, then one with high
$I$ and low $B$. Identify evidence that distinguishes barriers and choose
support for each. Do not use the same motivational message for both.

# Relational Support: Relationships as an Optional Resource {#relations}

## More connections do not necessarily mean more support

$R$ represents support the participant finds useful for the goal.
Many friends, comments, or likes do not necessarily indicate good support.
A person may prefer independent study while having a teacher available
when needed.

Evidence can include self-report, voluntary group participation, and
feedback on received support. There is no need to collect an address
book or reconstruct an entire social network simply to ask whether a
learner wants a practice partner.

## Different forms of support

Distinguish informational support, shared practice, organizational help,
and a sense of belonging. A peer who helps keep a schedule does not
replace a qualified evaluator. An active forum does not validate its advice.

| Type | Example | Limitation |
|---|---|---|
| Information | Share resources | Quality still needs checking |
| Practice | Work together on a small task | Do not complete it for the person |
| Organization | Send an agreed reminder | Avoid pressure |
| Expertise | Receive teacher feedback | Appropriate expertise is not automatic |
| Belonging | Join an optional discussion group | Preserve the right to leave |

## Do not publish the persona

Matching people does not require displaying their entire vectors.
Use goals, experience levels, and schedules that participants authorize
for sharing. Sensitive dimensions and psychological hypotheses should not
be public labels.

A group called "financially anxious people" can cause harm even if a
model predicts benefits from matching. A safer design groups people by a
chosen activity, such as discussing budgeting methods, with a clear
notice and voluntary participation.

## Support changes over time

A useful group this month may not be useful next month. Provide private
feedback, reporting mechanisms, and an accountable moderator. Do not
automatically increase social interaction when someone wants less sharing.

$R$ can be within the allowed support dimensions, but this only permits
offering an agreed resource. It does not permit pressure through friends,
unauthorized use of other people's data, or manufactured dependence on
a community.

**Exercise.** Design a study-partner invitation using only three data
fields. Identify what is displayed, what remains internal, and how to
leave. Ensure that declining matching does not remove access to teacher
feedback.

# Current Persona: State Estimates and Evidence Provenance {#current-persona}

## An estimate, not a final truth

The Current Persona is $\hat{\mathbf{P}}_t$, estimated at time $t$.
It can combine self-report, work products, events, and professional
feedback. Sources have different reliability and purposes. Do not
combine them into numbers while discarding provenance.

A record should explain when the evidence originated, which journey it
belongs to, whether its use is authorized, which criteria were applied,
and who may correct it. Keeping only the latest vector removes the
ability to explain change.

## Observation and inference

The system observes a submission, not "independent learning ability"
directly. A teacher can assess a work product using a rubric, but that
product remains evidence from one task. Preserve the distinction between
an event, an assessment, and a state hypothesis.

| Source | Strength | Risk |
|---|---|---|
| Self-report | Close to the participant's experience | May change over time |
| Events | Time-stamped observations | May be missing or duplicated |
| Work products | Close to actual task performance | May have been completed with assistance |
| Human guide | Professional judgment | Evaluators may disagree |
| Predictive model | Large-scale synthesis | Bias and distribution shift |

## Silence is not zero

No new events may reflect inactivity, device failure, offline work, or a
lack of collection permission. Record missingness rather than automatically
creating negative evidence.

As time passes, old estimates can become less certain. A prediction step
that increases uncertainty can represent this. The missing-data mechanism
still matters: intentional non-response differs from a random logging
failure.

## Participants can correct the model

Say "the system currently estimates..." and show the main evidence.
Participants may correct events, challenge an interpretation, or decline
use of a dimension. Corrections should reach pending decisions, not just
change the displayed score.

**Exercise.** Construct three estimates with the same logged event count
but different explanations: recording failure, offline work, and actual
non-completion. Explain how uncertainty and the next action differ.
Design an explanation screen that assigns no fixed personality trait.

# Context: Time, Conditions, and Scope of Use {#context}

## Separate the person from the situation

$\mathbf{C}_t$ includes conditions outside the persona state: an agreed
schedule, available tools, channel, journey stage, and relevant changes.
Separating context prevents a lack of opportunity from being treated as
a lack of will.

The same task may be manageable with a computer and an hour of free time,
but unsuitable on a phone with a few minutes available. A different
response does not prove a major persona change; the recommendation may
simply fail to fit the situation.

## Context also needs authorization

Predictive usefulness does not authorize collection. Location, financial
information, and personal calendars need clear purposes and boundaries.
Ask "What time works for you?" instead of reading the entire calendar.
Ask about required tools instead of inferring availability from a device.

Context needs a validity period. "Available Friday evening" should not
persist indefinitely. A field may need an expiry date or reconfirmation.
When context is missing, offering options may be better than confidently
choosing a time.

## Action-context interaction

The effect of $a$ can depend on $\mathbf{C}_t$. A reminder useful before
practice may be annoying after completion. Check the current state before
execution.

| Change | Risk if ignored | Response |
|---|---|---|
| Step already completed | Redundant reminder | Cancel the pending action |
| Schedule changed | Unwanted contact | Ask again if authorized |
| Tool unavailable | Infeasible recommendation | Find an alternative |
| Goal changed | Invalid progress measurement | Create a new target version |
| Permission changed | Unauthorized action | Block execution |

## Do not confuse context with identity

Someone considering price because expenses rose this month need not
become permanently "price sensitive." Time-limited labels and contextual
evidence prevent inappropriate recommendations months later.

In a multi-organization system, operational context also includes
`tenant_id`, domain, goal version, and access permissions. These are
governance conditions, not psychological coordinates. Similar vectors
from different tenants do not authorize cross-tenant retrieval.

**Exercise.** For a poorly performing recommendation, propose two
persona-based explanations and two contextual explanations. Distinguish
them using minimal data. Do not automatically add sensitive sources
merely to make discrimination easier.

# Uncertainty: Knowing What the System Does Not Know {#uncertainty}

## A coordinate needs an uncertainty statement

Two estimates with $\hat B=0.5$ may rest on very different evidence:
many reviewed work products in one case and two clicks in another.
An algorithm that reads only the number treats them as equivalent.

Uncertainty can arise from noisy observations, missing data, model
misspecification, evaluator disagreement, or a new population.
*Aleatoric uncertainty* concerns variability in the observation or
outcome process; *epistemic uncertainty* concerns limitations in knowledge
or the model. The distinction depends on the modeling setup. More clicks
do not necessarily resolve either problem.

## Variance and covariance

Store per-dimension uncertainty $\mathbf{U}_t$, or a covariance matrix
$\boldsymbol{\Sigma}_t$ when dependencies matter. Diagonal entries are
variances; off-diagonal entries describe joint variation in errors.
One assessment may affect both estimated behavior and need fulfillment.

A small variance does not establish correctness. A biased model can be
confidently wrong. Check interval coverage under a specified evaluation
procedure, compare with independent assessments, and monitor performance
on previously unseen groups.

## Uncertainty must affect decisions

| Situation | Potential response |
|---|---|
| Recent, consistent evidence | Suggest an approved action |
| Little evidence | Ask one useful question |
| Conflicting sources | Request review |
| Stale estimate | Reconfirm before acting |
| Outside the training domain | Do not extrapolate automatically |

Downweighting uncertain dimensions in a distance is only a summary
choice. It can make a gap look small because the dimension needing
support received less weight. Report evidence coverage and unresolved
dimensions. Do not translate "unknown" into "close to the goal."

## Gathering more information responsibly

If an extra question would not change the action, it may be unnecessary.
Balance the value of information against cost, participant burden, and
privacy. A short human review can be more useful than thousands of events.

Avoid one generic confidence score for all decisions. Selecting a reading
resource requires a different level of assurance from a credit decision
or an academic credential.

**Exercise.** Construct two states with the same mean but different
variances. Choose different actions and explain why. Give a case where
even a confident model needs human review because the action is risky.

# Mathematics from First Principles {#first-principles}

## Start with measurement

The mathematics begins not with deep learning but with a question:
which observations can be compared, in what units, and for which
decision? Without an answer, a formula merely hides assumptions.

A scalar is a single numerical quantity with a meaning: task count,
plan-completion rate, or rubric score. A vector groups quantities in a
defined order. A matrix can express relationships between multiple inputs
and outputs. A probability distribution represents possible values and
their uncertainty instead of treating one value as certain.

Do not add dimensions expressed in incompatible units. A possible
normalization for an observation $x$ on an agreed interval $[l,u]$,
where $u>l$, is:

$$
s(x)=\frac{x-l}{u-l}.
$$

The interval needs justification. Clipping values to its boundaries can
be useful but loses information. Do not clip erroneous input merely to
avoid reporting an error. Missing observations remain missing; they
should not become $0$ or $0.5$ by default.

Psychological ratings also need care. An ordinal response such as "low,
medium, high" establishes an order, not equal spacing. Encoding it as
$0,0.5,1$ introduces an additional assumption. Numerical operations do
not make that assumption valid.

## Why Euclidean distance uses squares and a square root

In a plane with orthogonal coordinate axes, the Pythagorean theorem gives
the length of a diagonal from two perpendicular components. Extending
this geometry to $n$ dimensions gives:

$$
D_E(\mathbf{x},\mathbf{y})
=\sqrt{\sum_{i=1}^{n}(x_i-y_i)^2}.
$$

Squaring prevents positive and negative differences from canceling.
The square root returns the result to the coordinate unit when the
coordinates share a unit. Treating normalized dimensions as orthogonal
axes is itself a modeling assumption. Geometric orthogonality is not
the same as statistical independence of measured variables.

For two illustrative coordinates, behavior and need fulfillment, let
$\mathbf{x}=[0.2,0.3]$ and $\mathbf{y}=[0.8,0.7]$.
The difference is $[0.6,0.4]$, so:

$$
D_E=\sqrt{0.36+0.16}=\sqrt{0.52}\approx0.7211.
$$

This does not mean the person is "$72.11\%$ deficient." It is a length
in the chosen representation. The largest difference also need not be
the best intervention target: another dimension may be more feasible
to support or may address the underlying barrier.

## Weights and masks

Nonnegative weights $w_i$ express relative importance. A binary mask
$m_i\in\{0,1\}$ selects dimensions within the evaluation scope:

$$
D_{w,\mathcal{K}}(\mathbf{x},\mathbf{y})
=\sqrt{\sum_{i\in\mathcal{K}}w_i(x_i-y_i)^2},
\qquad \mathcal{K}=\{i:m_i=1\}.
$$

Within $[0,1]^n$, each squared difference is at most $1$. Therefore:

$$
D_{\max}=\sqrt{\sum_{i\in\mathcal{K}}w_i}.
$$

With unit weights, this is $\sqrt{|\mathcal{K}|}$. If active weights sum
to $1$, the maximum is $1$. Use the same convention in the numerator
and denominator. Do not keep $\sqrt n$ after changing the weights.

The alignment score is:

$$
PAS_{w,\mathcal{K}}=1-\frac{D_{w,\mathcal{K}}}{D_{\max}}.
$$

Under the stated assumptions it lies in $[0,1]$. If all active weights
are zero, the denominator is zero and the score is **undefined**, not
automatically $1$. PAS is not a probability.

On the full vector space, zero weights allow distinct vectors to have
zero distance. Strictly, this is a pseudometric there; it is a metric
on the selected coordinates with strictly positive weights. This is
why reports must state the selected dimensions, not just a distance.

## Cosine and Mahalanobis answer different questions

Cosine similarity compares directions:

$$
\cos(\mathbf{x},\mathbf{y})
=\frac{\mathbf{x}^{\mathsf T}\mathbf{y}}
{\|\mathbf{x}\|\|\mathbf{y}\|}.
$$

Vectors $[0.2,0.2]$ and $[0.8,0.8]$ have cosine similarity $1$ despite
different magnitudes. Cosine can be useful for embedding retrieval,
but it need not measure an absolute competence gap. It is undefined
when either vector has zero norm. The commonly named cosine distance,
$1-\cos$, does not satisfy every metric property, notably the triangle
inequality.

Mahalanobis distance accounts for covariance and scale:

$$
D_M(\mathbf{x},\mathbf{y})
=\sqrt{(\mathbf{x}-\mathbf{y})^{\mathsf T}
\mathbf{S}^{-1}(\mathbf{x}-\mathbf{y})}.
$$

Define $\mathbf{S}$ explicitly, for example as a reference population
covariance matrix. It is not automatically the uncertainty covariance
of one participant. The inverse form requires an appropriate nonsingular
matrix; a singular estimate needs a justified, validated treatment.
Implementations generally solve a linear system rather than explicitly
forming the inverse. A sophisticated distance cannot repair a poor rubric.

## Conditional probability and Bayes' rule

Probability expresses uncertainty about a defined event.
$P(Y=1\mid X)$ is the probability of outcome $Y=1$ given $X$.
It does not, by itself, predict what would happen if we intervened on $X$.

Bayes' rule combines a prior distribution with evidence:

$$
p(\mathbf{P}\mid z)
=\frac{p(z\mid\mathbf{P})p(\mathbf{P})}{p(z)}.
$$

The numerator combines the likelihood of the observation under a state
with the prior. The denominator normalizes the posterior distribution.
This leads to filtering: predict the next state, then update the
distribution when a new observation arrives.

In one dimension, suppose the predicted mean is $0.4$, predicted
variance is $S^-=0.09$, observation is $z=0.7$, and observation-noise
variance is $R=0.04$. Under a direct-observation linear Gaussian model:

$$
K=\frac{S^-}{S^-+R}=\frac{0.09}{0.13}\approx0.6923,
$$
$$
\hat P^+=0.4+K(0.7-0.4)\approx0.6077,\qquad
S^+=(1-K)S^-\approx0.0277.
$$

Greater observation noise gives a smaller gain $K$. Without a new
observation, there is no measurement update, though prediction can still
increase variance. A Gaussian distribution is not bounded by $[0,1]$.
This example does not solve the boundary problem for persona scales.
A bounded model, suitable transformation, or different filtering method
may be necessary.

## Expected values, decisions, and costs

An expected value averages over possible outcomes; it is not a guarantee
for an individual. For a discrete outcome:

$$
\mathbb{E}[Y]=\sum_y y\,P(Y=y).
$$

Suppose a support action has a $0.3$ probability of progress and a
normalized cost of $0.1$. A simple utility model might compare
$0.3-0.1=0.2$ with alternatives. But benefits, costs, and harms must be
defined on compatible scales. Do not add a quality-of-life score to
revenue without stating the conversion.

Often it is clearer to maximize progress subject to constraints:

$$
\max_{a\in\mathcal{A}_{allowed}}
\mathbb{E}[\text{progress}(a)],
\quad
\text{cost}(a)\le b,\quad
\text{risk}(a)\le r_{\max}.
$$

An action is considered only after it meets hard constraints.
Violating a right cannot be offset by a larger expected commercial gain.

## Rates of change and a setpoint

A state can change over time. The rate of distance reduction over an
observation interval is:

$$
TV_t=\frac{TG_t-TG_{t+1}}{t_{t+1}-t_t}.
$$

The denominator must be positive and have a unit. A reduction from
$0.9$ to $0.7$ over two weeks gives $TV=0.1$ distance units per week.
Do not directly compare this with a daily rate without conversion.

Consider a noise-free one-dimensional model:

$$
P_{t+1}=P_t+k(P^*-P_t).
$$

Let $e_t=P^*-P_t$. Then $e_{t+1}=(1-k)e_t$ and
$e_t=(1-k)^t e_0$. Convergence requires $|1-k|<1$, or $0<k<2$.
For $0<k\le1$, each step approaches the target without overshooting.
This is a proof about the assumed linear model, not proof that people
will converge. Noise, delays, changing goals, and varying action effects
alter the conclusion.

## Causality through potential outcomes

Let $Y_i(1)$ be person $i$'s outcome with support and $Y_i(0)$ the outcome
without it. The individual treatment effect is:

$$
\tau_i=Y_i(1)-Y_i(0).
$$

We cannot observe both outcomes for the same person under the same
conditions. This is the fundamental problem of causal inference.
Random assignment can help estimate the average treatment effect:

$$
ATE=\mathbb{E}[Y(1)-Y(0)].
$$

If progress rates are $0.30$ in the supported group and $0.12$ in the
control group, the difference is $0.18$: **18 percentage points**,
not an $18\%$ relative increase. Sample size, sampling error, and design
determine how much confidence to place in the estimate.

Causal interpretation also needs well-defined interventions, consistent
outcome measurement, and appropriate assumptions about assignment and
interference. Each relevant alternative needs support in the data
for the comparison being made. In a community, support to one person
may affect another, so individual randomization may not satisfy a
no-interference assumption.

## Scores, the sigmoid, and calibration

A weighted score:

$$
PCS=\sum_j v_j s_j,\qquad v_j\ge0,\quad \sum_jv_j=1
$$

lies in $[0,100]$ when every $s_j$ is in $[0,100]$. This establishes its
range, not a probabilistic interpretation. A logistic mapping has the form:

$$
p=\sigma(a\,PCS+b),\qquad
\sigma(z)=\frac{1}{1+\exp(-z)}.
$$

Fit $a,b$ on an appropriate calibration set. A mapping into $[0,1]$
can still be wrong. One evaluation measure is the Brier score:

$$
BS=\frac1N\sum_{i=1}^{N}(p_i-y_i)^2.
$$

For $p=[0.2,0.8]$ and $y=[0,1]$,
$BS=(0.04+0.04)/2=0.04$.
The Brier score reflects multiple aspects of forecast quality.
Use reliability diagrams and checks across time, groups, and actions;
the Brier score alone does not establish calibration.

## Transition matrices

For $K$ segment states, a transition matrix has entries:

$$
T_{jk}=P(s_{t+1}=k\mid s_t=j).
$$

Each row is nonnegative and sums to $1$ when it can be estimated.
A row with no observations must not be divided by zero or automatically
filled with a self-transition. Report insufficient data.

The Markov assumption says the current modeled state is sufficient for
predicting the next state, conditional on that model. It does not say
people have no history. If history or context still matters, expand the
state or use a richer model.

**Integrated exercise.** Calculate $TG$, $PAS$, and $TV$ at three times
using one distance definition. Change the goal at the third time and
explain why the old and new rates cannot be joined without qualification.
Calculate a Brier score and state what it cannot prove. Identify one
measurement assumption, one probabilistic assumption, and one causal
assumption in the problem.

# Desired Persona and Setpoint: A Participant-Confirmed Reference {#setpoint}

## From a verbal goal to criteria

The Desired Persona translates a personal goal into a reference state
$\mathbf{P}^{*}$. Discuss the translation with the participant.
Do not substitute the company's "ideal customer" vector.

If Linh wants to complete a small project independently, the reference
can use agreed criteria for practice, support needs, and explanation.
Linh need not understand algebra to confirm the goal. The interface can
use plain-language descriptions while the system retains a versioned
mapping to coordinates.

## The target can be a range

A single point can be too rigid. Minh may choose an acceptable activity
range that fits everyday life rather than a maximum. The target is then
a set $\mathcal{G}$, with distance:

$$
D(\mathbf{P},\mathcal{G})
=\inf_{\mathbf{g}\in\mathcal{G}}D(\mathbf{P},\mathbf{g}).
$$

Entering the acceptable range does not imply that intervention should
intensify. The program may move to maintenance, reduce support, or end.
Agree on these possibilities beforehand.

## A setpoint is not an attractor

A reference value states the intended direction. It does not create a
physical attraction. Action effectiveness, circumstances, and participant
choices determine the actual trajectory.

An illustrative model is:

$$
\mathbf{P}_{t+1}
=\mathbf{P}_t+k_t\mathbf{M}\odot
(\mathbf{P}^{*}-\mathbf{P}_t)+\boldsymbol{\varepsilon}_t.
$$

$k_t$ is an assumed effectiveness coefficient, not a universal
psychological constant. Real effects may be negative, delayed, or
dimension-specific. Do not force $k_t\ge0$ merely to prevent the model
from representing harm.

## Goal versions and history

| Change | Record |
|---|---|
| Revised deadline | New goal-agreement version |
| Revised target level | New confirmed setpoint |
| Additional goal | Separate it or state the trade-off |
| Pause | Stop actions without judging the person |
| Completion | Assess and choose maintenance or closure |

When a setpoint changes, past states may be recomputed against the new
target for analysis, but label this as a reanalysis. Do not rewrite
historical reports to make the program appear successful.

**Exercise.** Design a point target and a range target for the same
journey. Explain when each is appropriate. Record a goal change, including
the reason, participant confirmation, and its effect on pending proposals.

# Transformation Gap: Distance for Support, Not Judgment {#transformation-gap}

## What does the distance answer?

Transformation Gap measures the difference between a current estimate
and a setpoint under specified dimensions, scales, and distance definition:

$$
TG_{\mathcal{K},t}
=D_{\mathcal{K}}(\hat{\mathbf{P}}_t,\mathbf{P}^{*}).
$$

It does not identify causes, measure human worth, or select an action by
itself. A large gap may reflect an overly broad goal, missing tools,
incorrect data, or insufficient evidence.

In the fitness example:

$$
\hat{\mathbf{P}}_0=[0.55,0.20,0.30,0.45,0.40,0.90,0.50],
$$
$$
\mathbf{P}^{*}=[0.75,0.90,0.80,0.85,0.80,0.90,0.70].
$$

Across all seven coordinates, $TG\approx1.068$.
For $\mathcal{K}=\{B,N,I,A,R\}$,
$TG_{\mathcal{K}}\approx0.970$.
The difference comes from evaluation scope, not a changed state.

## Decompose before acting

Squared differences show each dimension's contribution. The example
has differences of $0.70$ in $B$, $0.50$ in $N$, $0.40$ in $I$,
$0$ in $A$, and $0.20$ in $R$. This suggests that aspiration need not
be strengthened; identify a feasible step and the barriers to taking it.

Targeting the largest difference is not always optimal. If scheduling is
the main barrier, improving practical conditions may later improve
behavior. The gap is a map of questions, not a causal diagnosis.

## Comparisons require a common definition

| Keep consistent | Reason |
|---|---|
| Dimension-schema version | Coordinates need the same meaning |
| Distance and weights | Length needs the same convention |
| Setpoint | A changed target changes the gap |
| Selected dimensions | The PAS denominator depends on scope |
| Evidence window | Coverage changes can resemble progress |

Do not rank banking and education participants by $TG$. A shared
coordinate name such as $B$ does not imply the same measured quantity.
Even within one domain, different goals may not be comparable.

## Distance and uncertainty

If a state is a distribution, report a distribution of $TG$, for example
by sampling plausible states. Distance at the mean,
$D(\mathbb{E}[\mathbf{P}],\mathbf{P}^{*})$, does not capture
$\mathbb{E}[D(\mathbf{P},\mathbf{P}^{*})]$.
The estimated mean can be close to the target while substantial
uncertainty remains about the actual state.

**Exercise.** Find two states with equal $TG$ but differences in different
dimensions. Design different support for each. Explain why equal alignment
scores need not produce the same message.

# Persona Journeys: Trajectories, Maintenance, and Changes of Direction {#journey}

## Touchpoints are not the whole journey

A touchpoint map shows where someone interacts with a product.
A persona trajectory describes state changes over time. They complement
each other: a guidance session is a touchpoint; independently explaining
a data-processing step is an outcome to assess.

A transaction can occur in the middle of the trajectory. Buying a course
before acquiring competence, or shoes before forming a habit, can be
reasonable. Neither replaces competence or maintenance measures.

## Trajectories need not improve at every step

An interruption, new goal, or corrected evidence can increase the gap.
Do not force every dashboard line to rise. Retaining setbacks and their
possible explanations helps adjust support.

Separate real change, estimation change, and target change. If a teacher
discovers that AI completed a previous assignment, the estimate may be
revised downward. This improves the system's knowledge; it does not
necessarily mean the learner lost ability.

## Maintenance is a distinct phase

Meeting a criterion for one week does not establish a lasting behavior.
Observe outcomes with fewer reminders, in a new task, or after an agreed
interval. Agree on duration and maintenance criteria; do not keep a
person indefinitely because the program claims to be "building habits."

| Phase | Main question |
|---|---|
| Start | Which step is feasible and authorized? |
| Try | What does the evidence show? |
| Adjust | Have barriers or goals changed? |
| Maintain | Does progress continue with less support? |
| Close | Does the participant want to stop or choose a new goal? |

## Preserve history without retaining everything

Persona history needs versions and timestamps for explanation, but not
indefinite retention of all raw events. Evidence, features, and decision
logs may have different purpose-based retention periods.

A learning or resource graph represents relationships among skills,
lessons, and practice opportunities. It is not a personal trajectory.
Connecting these structures can improve resource selection, but
resource relevance does not prove learning.

**Exercise.** Draw a journey with two weeks of progress, one interruption,
and one goal change. Mark touchpoints, states, uncertainty, and target
versions. Report it without hiding the interruption or counting the new
target as an intervention effect.

# Deep Learning: Representation Learning Without Claims to Know the Whole Person {#deep-learning}

## Its main role is estimation

A model $f_\theta$ estimates state from authorized history and context:

$$
\hat{\mathbf{P}}_t=f_\theta(X_{\le t},\mathbf{C}_t).
$$

Possible approaches include feature aggregation, sequence models,
transformers, and graph representations. Starting with a large model
is not required. A rubric-based baseline with a few features is often
necessary to establish what complexity actually improves.

## Labels and learning objectives

Estimating a dimension requires a defined source of supervision.
If $B$ comes from a practice rubric, inputs must not include future
outcomes of the task being predicted. A model trained only on conversions
may learn purchase prediction, not a competence scale.

Possible learning objectives include next-observation prediction, feature
reconstruction, independent-assessment prediction, and sequence
representation learning. Each introduces assumptions. Good performance
on an auxiliary objective does not establish the psychological meaning
of a persona coordinate.

## Split by participant and time

Randomly splitting events can place the same person's history in both
training and test sets, producing overly optimistic results. Choose
splits appropriate to the claim: by person, by time, and sometimes by
organization.

| Check | Purpose |
|---|---|
| Simple baseline | Measure the benefit of complexity |
| Future-time test set | Assess temporal distribution shift |
| Unseen participants | Assess generalization |
| Independent assessment | Test coordinate meaning |
| Sparse-data groups | Examine coverage and uncertainty |

## Attention is not a sufficient explanation

An attention weight does not establish why a person acted. Product
explanations should connect to understandable evidence, model versions,
and limitations. An agent must not invent a psychological narrative to
make a black-box vector sound meaningful.

Monitor estimation error, evidence coverage, distribution shift, and
decision effects. A model can improve prediction while making support
less equitable. Evaluate both.

**Exercise.** Design a non-deep-learning baseline for one dimension.
Specify a prediction measure, an uncertainty measure, and a decision-quality
measure. State what results would justify keeping the baseline rather
than deploying the complex model.

# Bayesian Feedback: Prediction and State Updating {#bayesian-feedback}

## Prediction and update are separate steps

A filter maintains a state distribution, predicts the next state, and
incorporates observations. Updating only when an event arrives can leave
the system as confident after a month of silence as it was before.
Prediction reflects possible change outside what has been observed.

In a linear model:

$$
\hat{\mathbf{P}}^-_t=\mathbf{F}\hat{\mathbf{P}}_{t-1},
\qquad
\boldsymbol{\Sigma}^-_t=
\mathbf{F}\boldsymbol{\Sigma}_{t-1}\mathbf{F}^{\mathsf T}
+\mathbf{Q}_t.
$$

$\mathbf{F}$ is the state-transition matrix and $\mathbf{Q}_t$ the
process-noise covariance. Let observations follow
$\mathbf{z}_t=\mathbf{H}\mathbf{P}_t+\boldsymbol{\nu}_t$,
where $\mathbf{H}$ maps states to observations and observation noise has
covariance $\mathbf{R}_t$. The Kalman gain is:

$$
\mathbf{K}_t=
\boldsymbol{\Sigma}^-_t\mathbf{H}^{\mathsf T}
(\mathbf{H}\boldsymbol{\Sigma}^-_t\mathbf{H}^{\mathsf T}
+\mathbf{R}_t)^{-1}.
$$

## Updating the mean and covariance

$$
\hat{\mathbf{P}}_t=\hat{\mathbf{P}}^-_t+
\mathbf{K}_t(\mathbf{z}_t-\mathbf{H}\hat{\mathbf{P}}^-_t).
$$

The quantity in parentheses is the innovation: observed minus predicted
measurement. The Joseph covariance form supports numerical stability:

$$
\begin{aligned}
\boldsymbol{\Sigma}_t={}&
(\mathbf{I}-\mathbf{K}_t\mathbf{H})
\boldsymbol{\Sigma}^-_t
(\mathbf{I}-\mathbf{K}_t\mathbf{H})^{\mathsf T}\\
&+\mathbf{K}_t\mathbf{R}_t\mathbf{K}_t^{\mathsf T}.
\end{aligned}
$$

These formulas do not imply that Kalman filtering suits every domain.
The standard filter is appropriate when linear Gaussian assumptions are
an adequate approximation. Categorical observations, bounded coordinates,
or nonlinear dynamics may require other methods.

## Irregular observation intervals

Observations one day apart and one month apart should not automatically
use the same $\mathbf{Q}_t$. Model the elapsed time. When only some
dimensions are observed, update from that subset rather than inventing
measurements for the others.

| Problem | Response to consider |
|---|---|
| Sources have different noise | Source-specific $\mathbf{R}_t$ |
| Long interval | Increase uncertainty under the process model |
| Unusual observation | Check provenance; do not silently discard |
| Goal changed | Do not treat it as a state observation |
| Participant disputes an estimate | Review evidence and model assumptions |

## Feedback need not confirm the model

A system that believes a customer is ready to buy may send an offer and
then treat opening it as confirmation. It is learning from data generated
by its own policy. Distinguish evidence available independently of the
action from evidence influenced by it.

**Exercise.** Simulate five prediction steps without observations,
followed by one low-noise observation. Describe the mean and variance.
Explain why uncertainty may increase without any observed "bad event."

# Persona Conversion Score: Readiness for a Specified Action {#pcs}

## A business score with limited meaning

PCS aggregates signals relevant to a defined action. The paper's example
uses product fit, content engagement, campaign response, channel
performance, and intent. Its illustrative weights are:

$$
PCS=0.30s_P+0.25s_C+0.15s_K+0.08s_{Ch}+0.22s_I.
$$

For component scores $90,80,40,75,86.4$, the total is $78.008$,
or $78.0$ rounded to one decimal place. It is not a $78\%$ probability.

## Define the outcome before scoring

A conversion may mean starting an agreed savings step, completing a
purchase, or joining a gym. Each has different timing and conditions.
Do not reuse one PCS for every action simply because the customer is
the same.

Campaign and channel scores may reflect previous policies. People who
received more contact had more opportunities to generate signals.
Avoid equating low exposure with low interest.

| Question | Example answer |
|---|---|
| Which action? | Start the confirmed step |
| Within what period? | A predefined outcome window |
| Who is eligible? | Participants with appropriate authorization and conditions |
| Are signals available at prediction time? | No future outcomes as inputs |
| What is the score for? | Prioritize review, not pressure purchasing |

## PCS does not replace progress

Linh can have strong enrollment intent without gaining competence.
Minh can maintain agreed activity without buying a new membership.
Keep PCS alongside goal-relevant measures rather than treating it as
the sole measure of success.

A high score does not override risk. Readiness does not make an unsuitable
product acceptable. Apply constraints before optimization rather than
subtracting a few points and sending the offer anyway.

## Quality and maintenance

The illustrative weights are not deployment rules. Real use needs
appropriate data, stability checks, and group-level evaluation.
Version weight changes so historical scores remain interpretable.
Missing components require a disclosed, validated treatment; do not
silently substitute zero or renormalize weights.

**Exercise.** Create two customers with the same PCS but different
components. Suggest different communication for each. Explain why the
same aggregate score establishes neither the same probability nor the
same action effect.

# Calibration: When Can a Number Be Interpreted as a Probability? {#calibration}

## Being in range is not enough

A number in $[0,1]$ is not automatically a reliable probability.
Calibration examines agreement between predictions and observed
frequencies in comparable groups. If predictions near $0.8$ correspond
to an outcome rate near $0.4$, the model is overconfident.

Discrimination and calibration differ. A model can rank people well
while predicting incorrect probabilities. A model that always predicts
the base rate can be reasonably calibrated overall while providing
little discrimination.

## Three data sets

A clear workflow separates model training, calibration fitting, and
final testing. Selecting the best mapping on the test set and then
reporting performance on that same set is not independent evaluation.

Time-based splitting often matters. A probability that was accurate last
month may fail after policy, price, or population changes. Define the
observation window. Participants who have not yet completed follow-up
should not automatically receive a negative outcome label.

## Methods and checks

Platt scaling fits a sigmoid mapping. Isotonic regression fits a more
flexible monotone mapping. Flexible methods need enough data, particularly
in rare score regions. No method is always best.

| Tool | What it indicates | What it does not establish |
|---|---|---|
| Brier score | Overall probabilistic forecast error | Calibration in every group |
| Log loss | Penalty for confident errors | Policy value |
| Reliability diagram | Frequencies across prediction ranges | Causality |
| Group checks | Differences in forecast quality | Absence of every form of bias |
| Future-time evaluation | Temporal robustness | Permanent validity |

## Probabilities depend on the policy

$P(Y=1\mid X)$ estimated under an old interaction policy may change
under a new delivery policy. If actions affect the outcome, state the
policy conditions or use an appropriate action-conditioned model.
Observed probability is not the causal effect of support.

Good calibration for enrollment does not provide a probability of habit
formation. Long-term prediction requires long-term labels and appropriate
validation.

**Exercise.** Ten predictions are near $0.7$, but only three outcomes are
positive. Describe what this suggests and what remains uncertain with
such a small sample. Add checks on future data and sparse-data groups
before using probabilities for automated action selection.

# Generative AI: Grounded Support with Explicit Limits {#generative-ai}

## Content generation is not goal selection

Generative AI can explain, ask questions, summarize trade-offs, and create
practice materials. It should not decide who the participant must become.
Inputs include a confirmed goal, evidence-backed state, authorized scope,
and suitable resources.

Separate action selection from action wording. The selector chooses
"offer a small practice task"; the language model explains the task from
approved resources. Persuasive wording must not conceal an unreviewed action.

## RAG and resource graphs

Retrieval-augmented generation, or RAG, retrieves relevant documents
before generating a response. A resource graph can connect skills,
concepts, lessons, projects, and human guides. Embeddings retrieve
candidates; permission and domain filters must apply before documents
enter the model context.

Correct retrieval does not guarantee correct generation. Check that the
explanation reflects its sources and invents neither product terms nor
promises. In regulated domains, qualified reviewers need to approve
content boundaries.

## An output contract

| Field | Purpose |
|---|---|
| Selected action | Preserve intent during wording |
| Rationale | Connect to evidence and the confirmed goal |
| Sources | Allow verification |
| Limitations | Avoid guaranteed outcomes |
| Participant choice | Permit refusal or revision |
| Human escalation | Handle requests outside scope |

If sources are insufficient, report that explicitly. Do not generate a
helpful-looking response in place of missing information. Never invent
prices, fees, policies, or specifications in product advice.

## Evaluate content and outcomes

Evaluation includes correctness, goal relevance, comprehensibility,
privacy, pressure, and actual outcomes. A high click rate may reflect
fear-inducing wording; it is not sufficient evidence of benefit.

For learning, avoid completing work on the learner's behalf. Use
graduated hints, questions, and independent explanations.
AI-generated artifacts are not evidence of learner competence without
an independent assessment.

**Exercise.** Write an output contract for a two-product comparison.
Include missing specifications and a user request to change sources.
Check that the model reports insufficient data instead of filling gaps
with plausible guesses.

# AI Training Agents: Tool Coordination and Accountability {#training-agent}

## An agent is not an action

An AI Training Agent coordinates authorized evidence collection, resource
retrieval, support proposals, and feedback. NBTA names the selected
action. One agent may propose several actions; one action may be executed
without an autonomous agent.

"Training" here means supporting a person's practice, not conditioning
them toward a business objective. It also differs from training model
parameters. Product language must make these meanings clear.

## Bound tool permissions

An agent accesses tools and data according to its role. It may read
approved resources, but must not expand its own permissions, transfer
money, determine creditworthiness, or issue credentials. Consequential
actions require appropriate professional workflows.

| Step | Output to check |
|---|---|
| Read the goal | Current confirmed version |
| Read the state | Provenance and freshness |
| Select candidates | Authorized action set |
| Generate wording | Sources, limits, and refusal options |
| Request approval | A reasoned decision |
| Execute | Rechecked permission and current state |

## Memory and history

Agent memory is not an unrestricted conversation archive. Distinguish
journey state, confirmed choices, and content that need not be stored.
A superseded goal must not continue to drive recommendations.

Decision logs identify model version, proposal, approval gate, and
execution result without retaining unnecessary sensitive content.
Successful message delivery does not mean successful support.

## Errors and recovery

If retrieval fails, report insufficient grounding rather than inventing
resources. If delivery fails, retain the failure state and use a bounded
retry policy. If goals or permissions change while an action is pending,
cancel or review it again.

Trustworthiness includes saying no and stopping correctly, not just
completing many automated steps. An agent that requests human judgment
in an unclear situation is often preferable to one that always produces
a confident recommendation.

**Exercise.** Draw the states `proposed`, `review_required`, `approved`,
`executed`, `failed`, and `cancelled`. Identify transitions that need
authorization checks and failures that must not be retried automatically.

# Learning, Practice, and Reflection: Three Complementary Processes {#learning-practice-reflection}

## Learning for understanding

Learning provides concepts and explanations needed for a task.
Connect it to a specific gap rather than adding content because the
model predicts more viewing. A short explanation may be better than a
long course when the barrier is one concept.

Check understanding through explanation in the participant's own words,
recognition of an incorrect example, or application in a small task.
Viewing time is not sufficient evidence of understanding.

## Practice for evidence

Practice requires the participant to perform a step under appropriate
conditions. An agent can provide hints, but record the amount of
assistance. Work completed with many hints does not provide the same
evidence as independent work.

In retail, practice can mean trying a product and checking a criterion.
In finance, it can mean drafting a plan before deciding. In learning,
it can mean processing part of a dataset. Not every domain needs to turn
activity into a graded assignment.

## Reflection for revision

Reflection lets participants explain what worked, what was difficult,
and what they want to change. It does not require long writing or
private emotional disclosure. One useful question can be enough:
"Which step needs more support?"

| Process | Evidence | Common error |
|---|---|---|
| Learning | Explanation and comprehension | Equating viewing with learning |
| Practice | Action or work product | AI doing the work |
| Reflection | Barriers and revised choices | Forcing personal disclosure |

## Coordination without a rigid pipeline

A participant may practice first and then need more explanation.
Reflection may change the goal. Allow returns and skipped steps rather
than forcing a schedule to fill dashboard states.

A human guide can join wherever judgment is needed. Community support
is optional. Completing a cycle does not establish development; compare
evidence with the confirmed goal and maintenance criteria.

**Exercise.** Choose a skill. Design one learning activity, one practice
activity, and one reflection question for the same goal. Identify evidence
that would make you revise the plan rather than tell the learner to
"try harder."

# Community: Learning Together Without Exposing Private States {#community}

## The value of shared learning

A community can provide discussion, practice opportunities, and exposure
to different solutions. It should not be mandatory for receiving support.
Some people need privacy or prefer an individual pace.

Start with a shared activity, interaction rules, and an accountable
moderator. A group "working through a practice task together" is more
transparent than one named after a model's psychological inference.

## What needs to be shared?

Share only information authorized by participants and necessary for the
activity. Do not publish vectors, uncertainty estimates, financial
difficulties, or perceived-confidence values. Broad matching criteria,
such as schedule and goal, need not expose internal fields.

| Design area | Requirement |
|---|---|
| Participation | Voluntary, with a right to leave |
| Public profile | Minimal and participant-controlled |
| Feedback | Respectful rules without personal labeling |
| Moderation | An accountable person |
| Reporting | A private channel and clear handling |
| Evaluation | More than post counts |

## Quality of support

Popular advice can be wrong. Distinguish personal experience from expert
guidance. In regulated domains, peer advice must not replace professional
procedures.

Leaderboards may motivate some people while pressuring others to leave.
If used, state the purpose, provide an opt-out, and assess consequences.
Ranking is not a measure of personal worth.

## Evaluating community effects

Comparing people who choose to join with those who do not can be biased:
participants may already have different schedules or motivation.
Estimating effects needs an appropriate design and attention to spillovers.

Randomization by group rather than individual may be necessary.
This changes the number of independent units; sample-size planning and
analysis must account for the group structure.

**Exercise.** Design a practice group and an equivalent support option
for nonparticipants. Evaluate support quality without treating community
activity as evidence of independent competence.

# Human Coaches: Professional Judgment and Decision Rights {#human-coach}

## A person is not merely an approval gate

A Human Coach may be a teacher, mentor, or suitably qualified coach for
the domain. The role is not merely clicking "approve" on an AI proposal.
Review evidence, limitations, and participant choices, with authority
to change or block the plan.

A Coach Agent is software; a Human Coach is an accountable person with
a defined scope of competence. Do not use similar names to imply that
software advice comes from a real professional.

## Information for review

Provide a short summary: current goal, main evidence, freshness,
uncertainty, candidate action, rationale, risks, and alternatives.
A screen containing only a score and an approval button encourages
automation bias.

| What the reviewer needs | Why |
|---|---|
| Confirmed participant choices | Avoid replacing the goal |
| Unresolved evidence | Ask or check |
| Amount of AI assistance | Assess independence |
| Action boundaries | Avoid exceeding scope |
| Current permissions | Do not approve unauthorized actions |

## Expertise and workload

Not every operator is an expert in every domain. Define roles, training,
competence, and escalation paths. A learning mentor does not automatically
have financial or health expertise.

Sending every proposal to a person does not establish safety. Excessive
workload can produce routine approval. Use risk tiers, decision support,
and adequate review time.

## Decisions and feedback

Record reasons for approval, revision, or blocking, but do not treat every
human judgment as an infallible label. Check reviewer consistency and
provide a participant challenge mechanism.

In education, a guide can request independent explanation. In fitness,
an appropriately qualified professional can review program suitability.
AI should not generate individualized consequential decisions outside
its approved scope.

**Exercise.** Write a one-page review form for a moderate-risk action.
Include evidence that could justify rejection. Allow "insufficient
evidence" without forcing approval or permanent exclusion.

# Closed-Loop Personalization: Feedback with Stop Conditions {#closed-loop}

## The participant loop and the technical loop

The participant loop begins with voluntary participation and a goal:

$$
\text{Goal}\rightarrow\text{State}\rightarrow
\text{Support}\rightarrow\text{Experience}\rightarrow
\text{Feedback}.
$$

The technical loop observes, estimates, selects, checks, executes, and
updates. Connect the two: model outputs must not replace goals, and
participant feedback must be able to correct decisions.

The following English rendering replaces the Vietnamese-labeled
human-development figure in the source edition. The steps describe
coordination, not mandatory stages. Broader domains shown in the source
diagram do not automatically authorize applications beyond this book's
defined scope.

| Step | Human-centered development flow |
|---|---|
| 1. Start with the person | Consider participant-declared values, needs, resources, and constraints |
| 2. Confirm personal goals | Agree on priorities, time horizon, and success criteria |
| 3. Clarify context | Identify resources, practical conditions, and boundaries |
| 4. Select relevant domains | Choose the domains that serve the confirmed goal |
| 5. Run a domain-specific cycle | Estimate current state, confirm desired outcome, act, and review |
| 6. Coordinate optional support | The person decides; AI, communities, services, and tools assist |
| 7. Collect outcomes and evidence | Record meaningful progress and qualitative feedback |
| 8. Reflect across domains | Discuss what helped, what did not, and possible adjustments |
| 9. Update context | Use the new state, needs, opportunities, and constraints for the next cycle |

**Feedback paths:** updated context returns to context review. A changed
goal returns to participant confirmation. Withdrawal stops the affected
support. Neither a community nor a human coach is a compulsory step.

## Data generated by the policy

Once a system chooses content, subsequent data is partly shaped by that
choice. More messages create more opportunities to respond.
Learning only from selected recipients can reinforce unequal access.

Log actions, no-action choices, and how choices were made. Evaluation
should examine who responded, who was not offered support, why, and
with what consequences.

## Frequency and delay

An action may take time to affect an outcome. Hourly updates followed by
new proposals before the previous action has had time to work can create
noise. Match feedback windows and interaction frequency to the domain.

| Condition | Loop behavior |
|---|---|
| Outcome window not complete | Wait; do not declare failure |
| Criteria achieved | Choose maintenance or closure |
| Permission withdrawn | Stop affected actions |
| New risk | Block and review |
| Goal revised | Confirm the new reference |

## Stopping is a feature

A good support system does not keep people interacting indefinitely.
Completion, a wish to stop, or the absence of a suitable action can all
justify ending the loop.

**Exercise.** Write a cycle with observation, decision, execution, and
a delayed outcome. Place withdrawal inside it. Identify which data may
still be processed, which actions must be canceled, and what explanation
the participant needs.

# Agentic Segmentation: State Regions, Not Permanent Labels {#segmentation}

## Why retain segmentation?

Individual vectors do not eliminate the need to organize support in
groups. Segmentation helps discover structure, allocate resources, and
evaluate outcomes. A segment is a hypothesis about a region of state
space, not an immutable property of its members.

A person can be near several regions. Soft assignment may be more
appropriate than one label. With substantial uncertainty, report
insufficient evidence instead of forcing a classification.

## Agent roles and responsibility

Perception estimates state, Segmentation proposes groups, Action selects
support, and Guardrail checks execution. These are logical roles, not
a requirement for four separate models or services.

| Role | Output |
|---|---|
| Perception | State estimate and uncertainty |
| Segmentation | Clusters, stability, and interpretation |
| Action | Suitable candidates |
| Guardrail | Approve, request review, or block |

## Validate segments

The silhouette coefficient assesses separation under a chosen distance.
The adjusted Rand index, or ARI, compares two partitions.
Well-separated clusters do not necessarily differ in support needs.
Check domain meaning and real outcomes.

For bootstrap checks, compare assignments on a common reference set.
Do not compare raw K-means label numbers: labels can be permuted.
Over time, distinguish participant movement from movement of cluster
centers.

## Names and transitions

Language-model-generated names need caution. "Exploring the first step"
is preferable to an unconfirmed psychological condition.
Cluster names must not become clinical conclusions or a basis for
consequential decisions.

A transition matrix needs a consistent segmentation version, time window,
and population. A new model assigning a different cluster does not
automatically indicate personal development. Report rows without data
as unestimated.

**Exercise.** Design three segments around support needs. Specify
stability checks, non-stigmatizing names, and support for a person
between two segments.

# NBTA: The Best Next Action May Be No Action {#nbta}

## From a gap to a choice set

Next Best Transformation Action selects support appropriate to the goal
and conditions. Include "no intervention," "ask for more information,"
and "involve a person," not just sales choices.

A conceptual objective is:

$$
a_t^*=\arg\min_{a\in\mathcal{A}_{allowed}}
\mathbb{E}[D(\mathbf{P}_{t+1}(a),\mathbf{P}^{*})].
$$

This is not yet an operational algorithm. Estimate effects, costs,
risks, delays, and contact limits. Effect uncertainty can make a less
ambitious option preferable.

## Filter before ranking

Before scoring, exclude unauthorized actions, inappropriate domains,
unaffordable options, prohibited targeting, and unsupported claims.
Do not retain a harmful action with a risk penalty that a sufficiently
large commercial benefit can outweigh.

| Candidate | When it may fit |
|---|---|
| Short explanation | A specific information gap |
| Small practice task | Need to move from understanding to doing |
| Schedule adjustment | Changed time conditions |
| Human guidance | Need for professional judgment |
| No intervention | No clear benefit or already sufficient support |

## Short-term and long-term outcomes

An action that increases completion today may reduce independent ability
tomorrow if AI does the work. An offer can increase purchasing but damage
trust. Tie rewards to the agreed goal and appropriate horizon, not just
immediate response.

Contextual bandits suit some short-horizon decisions. Persistent action
effects may require sequential models. Reinforcement learning is not
justified merely because a journey has several steps; complexity needs
adequate data and a demonstrated benefit.

## Selection under uncertainty

Exploration stays within the approved action set. Thompson sampling draws
from beliefs about action performance; it does not solve safety, privacy,
or causal identification.

A bandit that maximizes raw progress rates and then compares with a
control group illustrates policy learning. It is not automatically
an algorithm that optimizes the causal uplift of each action.
That requires an appropriate counterfactual estimate, baseline, and
uncertainty treatment.

**Exercise.** Choose three support actions and one no-action option.
Define exclusions before scoring. Describe a case where no action is
better than the option with the highest conversion probability.

# Causal Uplift and Experiments: Did the Support Cause Progress? {#causal-uplift}

## Correlation does not answer the counterfactual

A ready customer may act without a recommendation. Contacting only such
customers can produce high success rates without creating additional value.

For action $a$ and covariates $x$, uplift is the conditional incremental
effect relative to a baseline:

$$
\tau(a,x)=
\mathbb{E}[Y(a)-Y(0)\mid X=x].
$$

The outcome $Y$ should reflect the agreed goal, not merely clicks.
In education, use an independent transfer task. In fitness, use
maintenance of agreed activity.

## Experiment before broad optimization

Predefine eligibility, action, baseline, timing, primary outcome, and
harm measures. Randomization balances observed and unobserved factors
in expectation. It does not eliminate measurement problems, attrition,
or interference between participants.

| Component | Requirement |
|---|---|
| Baseline | Ethically appropriate ordinary service |
| Outcome | Predefined and measured consistently |
| Assignment | Recorded, not changed in response to results |
| Sample size | Based on the effect worth detecting |
| Risk | Monitored with stopping conditions |
| Reporting | Includes unfavorable findings |

Do not withhold essential support to create a control group.
Respect the domain's professional and legal obligations.

## Adaptive policies

As a bandit changes assignments over time, simple average comparisons
can be misleading. Log action-selection probabilities and pre-action
context. Off-policy evaluation needs overlap: a new policy cannot be
reliably evaluated in regions with no relevant logged actions.

Inverse propensity weighting and doubly robust estimation may help
under suitable assumptions. Large weights and poor overlap make estimates
unstable. A positive point estimate is not permission to scale:
examine uncertainty and perform appropriate review.

## Limits on interpretation

A positive average effect does not mean everyone benefits.
Group-specific effects need enough data and protection against
post hoc subgroup selection. Long-term effects require long-term
observation, not extrapolation from a week of engagement.

When people withdraw, distinguish program withdrawal from missing
outcome measurement. Follow the prespecified analysis plan and current
data permissions. Intention-to-treat analysis can preserve randomized
assignment, but it does not solve missing outcomes or authorize continued
data collection.

**Exercise.** Design a two-group trial of guided practice. Define an
independent competence outcome and a burden measure. Explain withdrawal
handling and report the effect without presenting an average difference
as every individual's treatment effect.

# Guardrails: More Than a Score Threshold {#guardrails}

## Hard constraints and review

An impermissible action does not become permissible because a reviewer
expects it to succeed. Distinguish hard blocking, human review, and
eligibility for bounded automation.

Hard blocks can include absent authorization, cross-tenant access,
unconfirmed goals, and attempts to exploit vulnerability.
Review addresses evidence or risk requiring professional judgment.

| Condition | Outcome |
|---|---|
| No current permission | Block |
| Wrong domain or tenant | Block |
| Prohibited targeting | Block |
| Insufficient evidence | Ask or review |
| Risk requiring expertise | Review |
| All approved conditions satisfied | Allow automation within scope |

## A mask does not prove absence of effects

In a simulation, $m_V=m_E=0$ prevents the direct control term from
updating those coordinates. In real life, one experience may affect
many dimensions. Omitting $E$ from `targets` does not prove a message
has no emotional effect.

Review content, targeting, frequency, and harm feedback.
Prevention of manipulation is a system-wide requirement.

## Fairness and access

Excluding protected attributes from model inputs does not establish
fairness. Other variables may act as proxies. Evaluate opportunities
to receive support, forecast quality, outcomes, and harms across
appropriate groups under applicable rules.

A sparse-data group should not be abandoned simply because the model
is uncertain. Provide a support path that does not depend on the model.

## Recheck at execution

Permissions, state, and goals may change after approval.
The execution layer checks them again. Logs should identify which
condition failed, not merely record an unexplained "guardrail failed."

**Exercise.** Define test cases for missing permission, stale evidence,
pressuring content, a wrong tenant, and a changed goal. Identify hard
blocks and cases requiring human review.

# Customer 360 and Data: An Architecture That Preserves Scope {#customer360}

## Seven connected capabilities

The proposed architecture connects data sources, identity resolution,
Customer 360 profiles, personas and segments, journeys, activation,
and outcomes. These are logical capabilities, not proof that a
particular system already implements the complete framework.

Customer 360 is more than a final profile store. Preserve relationships
among evidence, goals, permissions, state versions, and decisions.
Business logic belongs in services; interfaces and controllers
coordinate input and output.

## Asynchronous ingestion

Tracking endpoints must enforce request-body size limits, verify scope,
validate schemas, and sanitize input before writing to S3 or a message
queue. Tracking endpoints do not connect directly to the database.
Asynchronous workers handle persistence and feature computation.

| Stage | Main checks |
|---|---|
| Ingestion | Body limit, schema, authorization |
| Queue | Tenant and event identifier |
| Processing | Deduplication, time handling, explicit errors |
| Features | Window and definition version |
| State | Evidence and uncertainty |
| Activation | Current permissions and guardrails |

## Identity and history

Identity resolution can be wrong. Matching names alone is not enough
to merge records. Preserve link evidence, merge history, and a way to
reprocess mistakes. Historical identities must remain traceable;
replacing a key must not destroy decision provenance.

Every data-access path needs tenant isolation and authorization.
UUIDs do not provide isolation when queries can read another tenant.
PostgreSQL 16 supports suitable UUID keys, foreign keys, indexes,
and JSONB, but schema design must reflect access patterns and retention
purposes.

## Time and reproducibility

Separate event time from ingestion time. Late events require explicit
recomputation rules; do not silently rewrite historical reports.
Stable identifiers and idempotent processing help prevent duplicate
support when events are replayed.

Version the schema, model, distance definition, and goal in state stores
and decision logs. The latest vector alone cannot explain why an agent
acted previously.

**Exercise.** Trace a late event and an identity correction.
Identify which steps recompute the persona, cancel proposals, and prevent
data from crossing tenant boundaries.

# Transformation Metrics: State, Change, and Value {#metrics}

## Six complementary measures

PAS measures alignment; TG measures distance; TV measures its rate of
reduction; CP is a calibrated probability of a defined outcome;
PD measures state change; TVa represents value. They answer different
questions.

$$
PAS=1-\frac{TG}{D_{\max}},
\qquad
TV_t=\frac{TG_t-TG_{t+1}}{\Delta t},
$$
$$
PD_t=D(\hat{\mathbf{P}}_t,\hat{\mathbf{P}}_{t-1}).
$$

A large PD is not inherently good. Someone may be moving away from the
goal, changing context, or simply being estimated more accurately.
"Persona Drift" here names state movement; it is distinct from
distribution shift in a machine-learning system.

## Meaningful outcomes

CP needs an action and time horizon. Enrollment probability is not a
probability of personal development. TV needs the same target, distance,
and time unit. Do not join rates before and after a goal change as an
unqualified series.

| Perspective | Example measures |
|---|---|
| Participant | Competence, maintenance, freedom of choice |
| Business | Cost, retention, relationship quality |
| Society | Access, harms, resource use |
| System quality | Uncertainty, errors, successful corrections |

## Multiple kinds of value

A weighted sum:

$$
TVa=w_cV_c+w_bV_b+w_sV_s
$$

is meaningful only with disclosed units and normalization rules.
Weights are governance choices, not natural constants.
The $V_c,V_b,V_s$ here are stakeholder-value measures, not the $V$
coordinate for participant values.

Often a separate dashboard and explicit constraints are clearer than
one aggregate score. Increased revenue cannot compensate for a rights
violation.

## Reporting without an illusion of progress

Include evidence coverage, withdrawals, goal changes, control outcomes,
and harms. Reporting only active participants can hide program failure.

Observed TG reduction is not a causal effect. Use "caused by the program"
only when the evaluation design supports that claim.

**Exercise.** Design a dashboard with at most eight measures.
Include a goal outcome, a harm measure, a rights measure, and a
data-quality measure. Identify which must not be optimized alone.

# Education and Personal Learning: From Content Viewing to Competence {#education}

## An illustrative goal and state

Linh is a fictional adult learner who often watches lectures but
completes little practice. The confirmed goal is to analyze a dataset
independently and present a small project. It is not to enroll in more
courses or spend more time on the platform.

Illustrative vectors:

$$
\hat{\mathbf{P}}_0=[0.65,0.25,0.35,0.55,0.40,0.85,0.45],
$$
$$
\mathbf{P}^{*}=[0.75,0.80,0.80,0.85,0.75,0.90,0.75].
$$

Across seven dimensions, $TG\approx0.906$ and $PAS\approx0.658$.
On $\{B,N,I,A,R\}$, $TG\approx0.829$ and $PAS\approx0.629$.
These are calculations on assigned numbers, not actual competence
assessments.

## Domain-specific evidence and definitions

$B$ can summarize practice; $N$ describes conditions and guidance;
$I$ concerns the next step; $A$ concerns the chosen future;
$R$ concerns optional support. Viewing counts cannot replace all these
dimensions.

| Synthetic evidence over 30 days | Count |
|---|---|
| Lectures viewed | 18 |
| Practice tasks started | 6 |
| Practice tasks completed | 2 |
| Professional feedback received | 1 |
| Independent projects submitted | 0 |

The counts suggest a need to move from content exposure to practice
with feedback. First determine whether tasks are too difficult,
schedules unsuitable, or tools unavailable.

## Designing the NBTA

One candidate is a small task involving part of a dataset, with criteria
approved by a teacher. Learning explains the missing concept; practice
requires Linh's own work; reflection asks what was difficult and what
needs to change.

AI provides graduated hints rather than completing everything and
recording success. Retain the assistance level and feedback provenance.
The learner can request human guidance or work independently.

## Learning architecture

![Proposed personal learning platform. Achieved competence still requires independent evidence.](assets/personal-learning-platform-architecture.png){width=95%}

The Experience layer provides access. Personal Training OS coordinates
goals and journeys; "OS" is an organizing metaphor, not a computer
operating system. Agents assist with tasks. The Personalization Engine
maintains estimates and proposes candidates. The Learning Graph connects
concepts, resources, and projects. Data preserves evidence. AI Foundation
supplies retrieval and generation capabilities. Consent, governance,
and teacher oversight apply across the layers.

The Learning Graph is not the persona vector. Semantic similarity to a
lesson does not establish that Linh needs it or has gained the skill
after viewing it. Outcomes shown in the architecture are evaluation
targets, not guaranteed results. The Coach Agent remains distinct
from a human teacher or mentor.

## Measuring effects and recognizing failure

A primary outcome might be independently completed transfer-task quality
under an agreed rubric. Secondary measures include sustained practice,
explanation quality, and burden. A control group receives ethically
appropriate ordinary support.

Common failures include AI doing the work, optimizing viewing time,
assessing learners through weak proxies, and preventing rubric challenges.
Inferred vectors alone must not determine academic credentials.

## Extension: learning English

For reading workplace documents, add task-specific skill criteria such
as main-idea comprehension, domain vocabulary, and explanation in the
learner's own words. Choose relevant texts, practice summarization,
then assess an unseen passage. Fluent AI-written answers do not prove
learner ability.

Conversation needs different criteria: appropriate dialogue tasks,
intelligibility, and handling situations. Do not copy a reading
measurement scheme directly into speaking assessment.

**Domain exercise.** Design a four-week journey with two hint levels,
one transfer task, and a goal review. Identify work completed by AI
and provide a separate support path for learners who do not join a
community.

# Retail Banking: Financial Capability Without Exploiting Insecurity {#banking}

## Personal goal and scope

An is a fictional participant who wants to plan expenses and establish
a savings step consistent with affordability. This is a support-design
example, not individualized financial advice or a product-selection rule.

Illustrative vectors:

$$
\hat{\mathbf{P}}_0=[0.50,0.10,0.20,0.50,0.30,0.80,0.40],
$$
$$
\mathbf{P}^{*}=[0.80,0.80,0.80,0.80,0.80,0.90,0.50].
$$

Across seven dimensions, $TG\approx1.140$ and $PAS\approx0.569$.
These are not credit scores and do not establish a clinical anxiety
condition.

## Authorized data and cautious interpretation

Transactions can provide cash-flow evidence when purpose and permissions
allow. Frequent balance checking does not establish hardship or an
intention to borrow. Ask about goals and conditions before recommending.

| Signal | Cautious interpretation |
|---|---|
| Frequent balance checks | Interest in monitoring; reason unknown |
| Reading budgeting material | Interest in information |
| Choosing a review schedule | Intent for a specific activity |
| Completing an agreed step | Behavioral evidence |
| Ignoring a recommendation | Unclear; do not increase pressure |

## NBTA and professional boundaries

Candidates include an explanation of expected expenses, a self-completed
plan, or a conversation with appropriately qualified staff.
An automatic transfer is considered only with participant confirmation,
eligibility, and the bank's authorized procedures.

AI must not invent fees, rates, terms, or guaranteed results.
Product information needs versioned sources. When information is
insufficient, explain that and use an official channel instead of
providing approximately correct advice.

Increased saving needs to be read alongside the ability to meet
obligations. More money set aside while essential spending is left
unfunded is not a good development outcome.

## Responsible evaluation

Evaluate execution of the self-chosen plan, understanding of trade-offs,
and perceived control. Revenue and product count represent only the
business perspective.

A trial baseline must not remove essential support. Monitor unsuitable
recommendations, pressure complaints, and domain-specific harms under
professional guidance. Persona models must not replace separate credit
risk and legal decision processes.

## Extension: changing cash flow

When income or expense timing changes, update context and reconfirm the
plan. Do not lower a character or commitment rating because saving pauses.

An extension may add task-specific financial-literacy criteria.
It needs a rubric, evidence, and its own authorization. Do not import
learning or shopping data from another tenant to "enrich" the profile.

## When the program should stop

Stop and explain when the participant withdraws, the goal is no longer
appropriate, or a recommendation would exceed the approved scope.
Insecurity must not become a reason for more frequent product solicitation.

**Domain exercise.** Write a goal agreement without a product name.
Identify necessary sources, blocking conditions, and progress evidence
that does not equate buying more products with success.

# Retail: Informed Decisions, Including the Choice Not to Buy {#retail}

## From hesitation to understanding trade-offs

Ha is a fictional customer comparing a household product.
The goal is a suitable choice within needs and budget, not checkout at
any cost. The name is rendered without the Vietnamese accent in this
English edition.

Illustrative vectors:

$$
\hat{\mathbf{P}}_0=[0.60,0.60,0.50,0.75,0.50,0.80,0.70],
$$
$$
\mathbf{P}^{*}=[0.75,0.85,0.80,0.90,0.80,0.90,0.75].
$$

Across seven dimensions, $TG\approx0.548$ and $PAS\approx0.793$.
This does not mean Ha has progressed more than An: the domains use
different measurement definitions.

## Evidence of consideration

| Synthetic event | Count |
|---|---|
| Product views | 24 |
| Searches | 9 |
| Comparisons | 7 |
| Reviews read | 15 |
| Add-to-cart events | 3 |
| Checkout starts | 2 |

These events show exploration and consideration, not the reason for
non-purchase. Ha may not understand a trade-off, lack an adequate
budget, or not need the product yet.

## NBTA does not default to a discount

For an information gap, offer a short comparison based on confirmed
priorities. Retrieve official specifications, report missing data,
and explain advantages and limitations.

If products do not fit, suggest keeping the existing option, waiting,
or not buying. This tests whether the engine supports decisions or
merely finds persuasive language to sell.

## Avoid pressuring personalization

Do not use low confidence to deliver fake scarcity, fabricated reviews,
or shame-inducing social comparisons. Distinguish advertising from evidence.

Be transparent about commercial influence. A sponsored recommendation
must not be described as a wholly independent conclusion.

## Outcomes after purchase and non-purchase

Assess perceived suitability, understanding of trade-offs, return
reasons, and ability to use the product. Higher sales accompanied by
more returns and complaints are not sufficient evidence of success.

Not buying can be correct. Offer voluntary feedback to non-buyers as well
as purchasers, so people helped to decline are not excluded from evaluation.

## Extension: sustainability and after-sales support

If a participant chooses repairability or longevity as a priority,
add criteria with verifiable sources. A marketing label alone does not
establish an environmental effect.

After purchase, a journey can shift to use and maintenance.
Confirm the new goal and data scope. Purchasing is not automatic consent
to usage tracking.

**Domain exercise.** Design a comparison with one unknown specification,
one cheaper option, and a no-purchase choice. Select a decision-quality
measure that is not simply revenue.

# Gyms and Fitness: Aspiration, Habits, and Professional Scope {#fitness}

## Minh's goal

Minh is a fictional participant who wants sustainable activity that fits
everyday life. This is not a personalized exercise prescription or
medical advice. Specific activities require appropriate professional
review when specialized judgment is needed.

The vectors repeat the gap example:

$$
\hat{\mathbf{P}}_0=[0.55,0.20,0.30,0.45,0.40,0.90,0.50],
$$
$$
\mathbf{P}^{*}=[0.75,0.90,0.80,0.85,0.80,0.90,0.70].
$$

High aspiration and limited behavior suggest looking for a feasible
step, not necessarily adding inspirational advertisements.

## Evidence and conditions

| Synthetic event | Count |
|---|---|
| Articles viewed | 17 |
| Videos viewed | 12 |
| Location searches | 5 |
| Pricing-page views | 3 |
| Trial bookings | 0 |

No booking has several possible explanations: schedule, access, an
unclear process, poor fit, or lack of interest. Zero does not justify
labeling someone lazy.

## Designing support

NBTA may explain the first-visit process, ask about suitable timing,
or offer a conversation with a qualified coach. Community participation
is optional. Specific activities remain within approved scope.

Learning explains a step; practice involves the suitable agreed plan;
reflection checks feasibility and needed changes. Do not use body-image
pressure or appearance comparisons.

## Calculating illustrative progress

On $\mathcal{K}=\{B,N,I,A,R\}$, initial distance is approximately
$0.970$. Suppose synthetic evidence for the following week gives:

$$
\hat{\mathbf{P}}_1=
[0.55,0.40,0.45,0.60,0.40,0.90,0.60].
$$

The new gap is about $0.667$, PAS about $0.702$, and gap reduction
about $0.302$ per week. Without a suitable causal evaluation,
these numbers do not show that AI caused the improvement.

## Evaluate beyond the transaction

A membership card does not establish a habit. Observe activity under
the self-chosen plan, maintenance, and feedback on suitability.

The organization needs domain-appropriate harm monitoring and
professional escalation. Agents must not diagnose from sensor data or
independently revise individualized professional guidance.

## Extension: maintenance when schedules change

When Minh changes work shifts, reconfirm context. Pause or select a
different support format if appropriate. A less demanding target
must not be counted as behavioral improvement merely because the
gap shrank.

Resources, coaches, and groups can be connected in a graph to find
support. Do not transfer an academic rubric or purchase-distance
definition directly into habit measurement.

**Domain exercise.** Design a support journey without individualized
exercise instructions. Identify professional-review points, schedule
change handling, and assessment after support is reduced.

# Extending to New Domains: Preserve Principles, Redesign Measurement {#domain-extension}

## Do not copy the vector and rename it

A new domain needs its own goals, evidence, permissions, and risk
assessment. Retaining seven dimension names can aid discussion but
does not establish equivalent measurement. Replacing "exercise session"
with "study session" is not a complete adaptation.

Professional skill development might aim at independently completing
a work task. Use authorized work products and feedback, not automatic
access to all employee emails or calendars. A persona must not become
a secret score for recruitment or consequential employment decisions.

## An extension process

| Step | Required question |
|---|---|
| Goal | Who chooses it, and can it be revised? |
| Domain | Which decisions are in scope? |
| Dimensions | Are definitions and evidence available? |
| Distance | What does the gap mean? |
| Actions | Is no intervention an option? |
| Governance | Who is accountable? |
| Evaluation | What would support or refute benefit? |

Begin with a narrow goal. A general-purpose platform is not necessary
before support has shown value. A small human-guided pilot can test
criteria before automation.

## Example: using a digital service

The goal can be independently completing a necessary operation, not
increasing time spent. Evidence includes independent execution and
understanding of choices. NBTA can offer a short guide, accessible
interaction, or human help.

If the operation is rarely needed, low frequency is not failure.
Metrics should serve the goal, not force the goal to serve the metric.

## Where the framework should not be stretched

The framework alone does not justify mental-health diagnosis, credit
decisions, academic credentials, or consequential personnel assessment.
Those applications need their own standards, professional expertise,
and legal review.

Do not prioritize a data-rich domain that lacks appropriate authorization
or meaningful criteria merely because a model is easy to train.

**Exercise.** Write a one-page proposal for a new domain.
Include one prohibited decision, one source you will not collect,
and one condition that would prevent deployment.

# Ethics, Law, and Governance of Autonomy {#ethics}

## Ethics belongs in the mechanism

"Human-centered" is not enough if optimization still targets conversions
alone. Autonomy needs operational constraints, interfaces, execution
checks, and reporting.

Core principles include self-chosen goals, transparency, data minimization,
non-exploitation of vulnerability, correction rights, and stopping rights.
Keep commercial goals separate so conflicts remain visible.

## Law depends on the use case

The GDPR and EU AI Act are examples of frameworks that may apply to
personal data and certain AI systems. Applicability depends on jurisdiction,
organizational role, purpose, and decision type. Do not assume every
persona application has the same risk classification or that all
automated profiling is regulated identically.

This book is not legal advice. Qualified professionals should review
the use case and current legal requirements before deployment.

## Responsibility throughout the lifecycle

| Role | Responsibility |
|---|---|
| Program owner | Goals and boundaries |
| Participant | Choice, revision, and refusal |
| Domain specialist | Criteria and domain risks |
| Data team | Quality, distribution shift, and uncertainty |
| Operations team | Permissions, execution, and errors |
| Audit team | Evidence and compliance |

Do not assign responsibility to "the AI." Organizations design and
operate models, policies, and tool permissions.

## Explanation and challenge

Participants need the purpose, main data used, reasons for recommendations,
and ways to correct errors. Explanation need not expose every parameter,
but must be sufficient to identify incorrect data or an inappropriate
action.

Provide an accountable challenge channel. A generated response defending
the model is not an adequate final appeal decision.

## Prevent commercial exploitation of vulnerability

A signal of disadvantage should trigger limits on pressure, not a
stronger sales strategy. Review both content and delivery:
neutral wording can still become intrusive when sent too often.

**Exercise.** Write one blocking rule, one transparency rule, and one
correction right. Define auditable evidence for each.
"A person approved it" is not a substitute for evidence that the rules
were enforced.

# From Hypotheses to Research and Deployment {#research-deployment}

## A proposed framework must be falsifiable

Dynamic personas may help, but the claim needs testing.
If a simpler model supports people better, choose it.
Research should produce trustworthy findings, not ensure that every
experiment confirms the framework.

Questions derived from the paper include: do dynamic states predict
better than static labels; can dimensions be measured repeatedly;
does goal-conditioned content produce incremental benefit;
do correction rights improve trust; and does the program create
long-term value?

## A minimum research design

| Item | Content |
|---|---|
| Hypothesis | A claim that can be rejected |
| Baseline | An appropriate existing method |
| Measurement | A rubric and independent evidence |
| Experiment | Assignment, timing, and risk monitoring |
| Analysis | Effect estimates and uncertainty |
| Reporting | Benefits, harms, and limitations |

Do not reuse the same quantity as input and outcome and call the result
validation. If PAS includes task completion, correlating PAS with the
same completed tasks does not show that the model understands
personal development.

## Deploy in stages

Begin by designing goals and measures with domain professionals.
Retrospective analysis checks data quality. Shadow mode generates
proposals without delivering them, allowing authorization and error
checks without intervention effects.

Next, run a bounded trial of approved actions with harm monitoring
and effective stopping. Expand only when evidence is sufficient and
operations can handle failures.

Shadow mode does not establish intervention benefit.
Off-policy evaluation does not replace every live experiment.
Positive results on synthetic data demonstrate only specified
algorithmic behavior.

## Conditions for not deploying

Do not deploy without confirmed goals, clear data authorization,
meaningful measurements, correction channels, or the ability to stop
after withdrawal. Do not expand when benefit fails to exceed the
baseline or harms remain uncontrolled.

**Exercise.** Write a study plan with one primary outcome, two harm
measures, and stopping criteria. State what findings would justify
keeping the baseline instead of adding more AI.

# Reproducible Practice: Checking Formulas and Program Behavior {#reproducible-practice}

## What the code demonstrates

The example uses only the Python standard library. It checks distances,
normalization, rates, and a scalar filtering update.
It does not simulate real people or validate the framework's effectiveness.

Save the block as one file and run it with Python 3.10 or later.
Functions reject invalid input explicitly. They do not clip errors,
invent defaults, or return a success-shaped score for an empty
evaluation scope. The code is identical to the Vietnamese edition
so the numerical example remains directly reproducible.

```python
from math import isclose, isfinite, sqrt
from collections.abc import Callable, Sequence


def gap_and_alignment(
    current: Sequence[float],
    desired: Sequence[float],
    weights: Sequence[float],
) -> tuple[float, float]:
    """Return weighted gap and alignment on selected dimensions."""
    if not current or not (
        len(current) == len(desired) == len(weights)
    ):
        raise ValueError("Vectors must have equal nonzero length")
    for vector in (current, desired):
        if any(not isfinite(x) or not 0 <= x <= 1 for x in vector):
            raise ValueError("Coordinates must be finite in [0, 1]")
    if any(not isfinite(w) or w < 0 for w in weights):
        raise ValueError("Weights must be finite and nonnegative")
    total = sum(weights)
    if not isfinite(total) or total <= 0:
        raise ValueError("At least one weight must be positive")
    squared = sum(
        w * (x - y) ** 2
        for x, y, w in zip(current, desired, weights)
    )
    gap = sqrt(squared)
    return gap, 1 - gap / sqrt(total)


def velocity(before: float, after: float, elapsed: float) -> float:
    """Return gap reduction per positive unit of elapsed time."""
    if any(not isfinite(x) for x in (before, after, elapsed)):
        raise ValueError("Values must be finite")
    if before < 0 or after < 0 or elapsed <= 0:
        raise ValueError("Gaps must be nonnegative; elapsed positive")
    return (before - after) / elapsed


def scalar_update(
    mean: float, predicted_variance: float,
    observed: float, observation_variance: float,
) -> tuple[float, float]:
    """Apply one direct-observation Gaussian update without clipping."""
    values = (mean, predicted_variance, observed, observation_variance)
    if any(not isfinite(x) for x in values):
        raise ValueError("Values must be finite")
    if predicted_variance < 0 or observation_variance <= 0:
        raise ValueError("Invalid variances")
    gain = predicted_variance / (
        predicted_variance + observation_variance
    )
    updated_mean = mean + gain * (observed - mean)
    updated_variance = (1 - gain) * predicted_variance
    return updated_mean, updated_variance


def expect_invalid(call: Callable[[], object]) -> None:
    """Assert that an invalid-input case raises ValueError."""
    try:
        call()
    except ValueError:
        return
    raise AssertionError("Invalid input was accepted")


current = [0.55, 0.20, 0.30, 0.45, 0.40, 0.90, 0.50]
desired = [0.75, 0.90, 0.80, 0.85, 0.80, 0.90, 0.70]
following = [0.55, 0.40, 0.45, 0.60, 0.40, 0.90, 0.60]
mask = [0, 1, 1, 1, 0, 1, 1]

gap0, alignment0 = gap_and_alignment(current, desired, mask)
gap1, alignment1 = gap_and_alignment(following, desired, mask)
assert isclose(gap0, sqrt(0.94))
assert isclose(gap1, sqrt(0.445))
assert isclose(alignment0, 1 - sqrt(0.94 / 5))
assert isclose(alignment1, 1 - sqrt(0.445 / 5))
assert isclose(velocity(gap0, gap1, 1), gap0 - gap1)
assert gap_and_alignment([0, 0], [1, 1], [2, 3])[1] == 0
assert gap_and_alignment([1, 1], [1, 1], [2, 3])[1] == 1
assert gap_and_alignment(current, desired, mask) == (
    gap_and_alignment(
        [0.99, *current[1:4], 0.01, *current[5:]],
        desired,
        mask,
    )
)

mean, variance = scalar_update(0.4, 0.09, 0.7, 0.04)
assert isclose(mean, 0.6076923076923076)
assert isclose(variance, 0.027692307692307697)
assert isclose(sum([0.3, 0.25, 0.15, 0.08, 0.22]), 1)
pcs = sum(
    w * score
    for w, score in zip(
        [0.3, 0.25, 0.15, 0.08, 0.22],
        [90, 80, 40, 75, 86.4],
    )
)
assert isclose(pcs, 78.008)
brier = sum((p - y) ** 2 for p, y in [(0.2, 0), (0.8, 1)]) / 2
assert isclose(brier, 0.04)

expect_invalid(lambda: gap_and_alignment([], [], []))
expect_invalid(lambda: gap_and_alignment([0], [0, 1], [1]))
expect_invalid(lambda: gap_and_alignment([2], [0], [1]))
expect_invalid(lambda: gap_and_alignment([0], [0], [-1]))
expect_invalid(lambda: gap_and_alignment([0], [0], [0]))
expect_invalid(lambda: gap_and_alignment([float("nan")], [0], [1]))
expect_invalid(lambda: gap_and_alignment([0], [0], [float("inf")]))
expect_invalid(lambda: velocity(1, 0, 0))
expect_invalid(lambda: scalar_update(0.4, -1, 0.7, 0.04))

print(f"TG0={gap0:.3f}; PAS0={alignment0:.3f}")
print(f"TG1={gap1:.3f}; PAS1={alignment1:.3f}")
print(f"TV={velocity(gap0, gap1, 1):.3f} per week")
print(f"Posterior mean={mean:.4f}; variance={variance:.4f}")
print(f"PCS={pcs:.3f}; Brier={brier:.3f}")
print("All checks passed")
```

## Expected output

```text
TG0=0.970; PAS0=0.566
TG1=0.667; PAS1=0.702
TV=0.302 per week
Posterior mean=0.6077; variance=0.0277
PCS=78.008; Brier=0.040
All checks passed
```

The mask tests only the mathematical component.
Changing $V,E$ does not change this selected-coordinate distance;
that does not prove a real message has no effect on those dimensions.

## Extending the tests

Add cases for late events, missing observations, goal changes, withdrawal,
and blocked actions. Those need an explicit application-state model,
not just vector arithmetic.

For filtering without observations, add a prediction step such as
$S^-=S+Q\Delta t$ under a suitable process-noise model. Do not update
with an invented observation. Causal evaluation needs a separate
experimental design; assertions on synthetic numbers do not establish
real-world effects.

**Exercise.** Change weights and verify the PAS denominator.
Test that masking every dimension raises an error.
Explain which parts of a real application are not covered by these checks.

# Conclusion: Better Models Must Preserve Human Agency {#conclusion}

A dynamic persona vector organizes questions, evidence, and support.
It brings behavior, needs, intent, and conditions into view rather than
using a fixed label. Modeling power must not become authority to define
a person.

Begin with voluntary participation and a confirmed goal. The current state
is always a limited estimate. Distance has meaning only within a defined
measurement scheme. Actions need authorization, evidence, boundaries,
and a no-intervention option.

AI learns representations, updates estimates, and generates explanations.
Communities and human guides provide different forms of support.
No component establishes development by itself.
Assess meaningful outcomes and use causal evaluation when attributing
effects to the program.

The four domains share principles but differ in measurement.
Education needs independent competence evidence; banking needs
suitability and professional limits; retail needs informed choices;
fitness needs maintenance and appropriate expertise.
There is no universal metric for ranking people across these domains.

A trustworthy system can acknowledge uncertainty, ask fewer questions,
stop at the right time, and accept a change of direction.
It does not treat every refusal as an obstacle to overcome.

**Final principle.** Use the vector to support a conversation.
Do not use the conversation to force a person into the vector.
A system that increases conversions while reducing freedom of choice
has not achieved this book's purpose.

# Glossary and Reading Map {.unnumbered #glossary}

| Term | Meaning in this book |
|---|---|
| Persona | Limited state representation for a domain and time |
| Self Opt-In | Voluntary participation within an understandable scope |
| Personal Goal | Participant-chosen or participant-confirmed objective |
| Current Persona | Current state estimate |
| Desired Persona | Representation of the desired state |
| Setpoint | Reference value; not automatically an attractor |
| Transformation Gap | Distance under specified coordinates and a defined measure |
| PAS | Normalized alignment; not a probability |
| TV | Rate of gap reduction per unit time |
| PD | State movement; not necessarily progress |
| PCS | Readiness score for a specified action |
| CP | Outcome probability with a horizon and validation |
| NBTA | Next support action within the allowed set |
| Uplift | Incremental causal effect relative to a baseline |
| Guardrail | Authorization, risk, and scope check before execution |
| Learning Graph | Relationships among skills, resources, and practice opportunities |
| RAG | Retrieval-augmented generation using relevant sources |
| Human Coach | Accountable person with appropriate domain expertise |

## How the concepts connect {.unnumbered}

Goals and permissions define what is allowed.
Vectors, context, and uncertainty define what is known.
The gap identifies questions requiring support.
NBTA and guardrails determine what may be done.
Learning, practice, reflection, and human support create experiences.
Feedback updates the model; experiments evaluate effects.

Read [mathematics](#first-principles) to understand how a measure is
constructed, [causality](#causal-uplift) to understand what it permits
you to conclude, and [governance](#ethics) to decide whether it should
be used. None of these questions replaces the others.

## Technical terminology across disciplines {.unnumbered}

The following distinctions prevent common translation and interpretation
errors. Plain language should clarify a technical term without changing
its meaning.

| Term | Technical distinction | Plain-English explanation |
|---|---|---|
| Autonomy | Self-determination, not automated operation | The person has meaningful choice |
| Competence | Ability, not mere task completion | The person can perform the relevant task |
| Relatedness | An SDT need, not a contact count | Feeling connected and supported |
| Self-efficacy | Task-specific belief in capability | Belief that one can carry out the task |
| Construct | A concept requiring operationalization | An idea we need evidence to measure |
| Latent state | Not directly observed | A state inferred from observable evidence |
| Rubric | Explicit assessment criteria | A shared guide for judging work |
| Metric | A mathematical distance with stated properties | A rule for comparing points |
| Performance measure | An evaluation quantity | A number used to check how well something works |
| Estimate | A value inferred from data | What the evidence currently suggests |
| Variance | Squared spread under a model | How widely values may vary |
| Covariance | Joint variation, not causation | How quantities vary together |
| Calibration | Agreement of probabilities with frequencies | Whether forecast chances match outcomes |
| Discrimination | Ranking ability in prediction | Whether forecasts separate different outcomes |
| Data leakage | Unavailable or test information contaminates learning | The model gets information it should not have |
| Embedding | A learned numerical representation | Coordinates used to capture useful relationships |
| Policy | A rule for selecting actions | How the system chooses what to do |
| Propensity | Probability of action assignment in causal analysis | How likely an action was to be selected |
| Counterfactual | Outcome under an alternative action | What would happen under another choice |
| Interference | One person's treatment affects another's outcome | Participants can influence each other's results |
| Idempotency | Repetition does not duplicate the intended effect | Processing again does not send twice |
| Provenance | Recorded origin and processing history | Where evidence came from |

*Confidence* requires particular care. Participant confidence is a
psychological report; a model's confidence is a statistical or algorithmic
statement. A confidence interval and a Bayesian credible interval also
have different interpretations. Do not present an unspecified "confidence
percentage" as if these meanings were interchangeable.

Likewise, *prediction* concerns an outcome under stated conditions;
*causal inference* concerns the effect of changing an action.
*Conversion* is a defined commercial or behavioral event;
*transformation* in this framework is progress toward a participant's
agreed goal. Neither implies the other.

# Appendix: Publication Checks {.unnumbered #publishing-checks}

## Structural checks {.unnumbered}

The manuscript uses YAML metadata, level-one chapter headings, level-two
sections, LaTeX mathematics, and relative links. Pandoc generates chapter
numbers and the table of contents. Do not type chapter numbers or page
numbers manually.

For a quick HTML review:

```bash
pandoc docs/research-papers/human_persona_as_dynamic_vector_en.md \
  --standalone --toc --number-sections \
  --resource-path=docs/research-papers \
  --mathjax \
  -o /tmp/human-persona-book-en.html
```

HTML supports structural and mathematical review, but it does not
establish A4 page count. Inspect the generated PDF:

```bash
pdfinfo docs/research-papers/human_persona_as_dynamic_vector_en.pdf
pdftotext -layout \
  docs/research-papers/human_persona_as_dynamic_vector_en.pdf \
  /tmp/human-persona-book-en.txt
```

Accept the length requirement only when `Pages` is greater than $50$
and pages are A4. Also inspect the opening contents, domain chapters,
formulas, and examples. Many empty pages do not make a useful book.

## Content checks {.unnumbered}

Check dedicated concept chapters, the seven coordinate definitions,
mathematical assumptions, references, synthetic-data notices, participant
rights, evaluation, and stopping mechanisms.

Execute the example, including invalid-input tests.
Domain results must agree with the selected coordinates and target
versions. Compare English and Vietnamese chapter structure to detect
omissions. Review terms in context, not only with a spelling checker.

Remove temporary review files after validation. Keep Markdown and PDF
as the publication artifacts, not intermediate logs.

# References and Development Sources {.unnumbered #references}

These works support the concepts and methods discussed.
They are not direct evidence that the combined framework in this book
has been validated empirically.

## Development source {.unnumbered}

Nguyen, T. (2026). *Persona as a Vector: A Setpoint Theory of Human
Identity, Personalization, and Transformation in Marketing 8.0*.
Author's research manuscript. English rendering of the source title.
[Vietnamese paper](persona_as_a_vector_marketing_8.0_vi.md).
[Vietnamese book](human_persona_as_dynamic_vector_vi.md).

## Psychology and design {.unnumbered}

Cooper, A. (1999). *The Inmates Are Running the Asylum*.
Sams Publishing. A reference for representative design personas,
which differ from the individual state representations used here.

Deci, E. L., & Ryan, R. M. (2000). The "what" and "why" of goal pursuits:
Human needs and the self-determination of behavior.
*Psychological Inquiry*, 11(4), 227-268.
<https://doi.org/10.1207/S15327965PLI1104_01>

Higgins, E. T. (1987). Self-discrepancy: A theory relating self and affect.
*Psychological Review*, 94(3), 319-340.
<https://doi.org/10.1037/0033-295X.94.3.319>

Jung, C. G. (1959). *The Archetypes and the Collective Unconscious*.
Collected Works, Vol. 9, Part 1. Princeton University Press.

Lewin, K. (1951). *Field Theory in Social Science: Selected Theoretical
Papers*. Harper & Brothers.

Markus, H., & Nurius, P. (1986). Possible selves.
*American Psychologist*, 41(9), 954-969.
<https://doi.org/10.1037/0003-066X.41.9.954>

Prochaska, J. O., & DiClemente, C. C. (1983). Stages and processes of
self-change of smoking: Toward an integrative model of change.
*Journal of Consulting and Clinical Psychology*, 51(3), 390-395.
<https://doi.org/10.1037/0022-006X.51.3.390>

Bandura, A. (1977). Self-efficacy: Toward a unifying theory of behavioral
change. *Psychological Review*, 84(2), 191-215.
<https://doi.org/10.1037/0033-295X.84.2.191>
Included for the English edition's clarification of task-specific
confidence; it does not validate the illustrative $E$ coordinate.

## Data, representations, and clustering {.unnumbered}

Christen, P. (2012). *Data Matching: Concepts and Techniques for Record
Linkage, Entity Resolution, and Duplicate Detection*. Springer.
<https://doi.org/10.1007/978-3-642-31164-2>

Hochreiter, S., & Schmidhuber, J. (1997). Long short-term memory.
*Neural Computation*, 9(8), 1735-1780.
<https://doi.org/10.1162/neco.1997.9.8.1735>

Hubert, L., & Arabie, P. (1985). Comparing partitions.
*Journal of Classification*, 2, 193-218.
<https://doi.org/10.1007/BF01908075>

Rousseeuw, P. J. (1987). Silhouettes: A graphical aid to the interpretation
and validation of cluster analysis.
*Journal of Computational and Applied Mathematics*, 20, 53-65.
<https://doi.org/10.1016/0377-0427(87)90125-7>

Vaswani, A., et al. (2017). Attention is all you need.
*Advances in Neural Information Processing Systems*, 30.
<https://arxiv.org/abs/1706.03762>

## Estimation, calibration, and decisions {.unnumbered}

Kalman, R. E. (1960). A new approach to linear filtering and prediction
problems. *Journal of Basic Engineering*, 82(1), 35-45.
<https://doi.org/10.1115/1.3662552>

Platt, J. C. (1999). Probabilistic outputs for support vector machines
and comparisons to regularized likelihood methods.
In *Advances in Large Margin Classifiers*, 61-74. MIT Press.

Niculescu-Mizil, A., & Caruana, R. (2005). Predicting good probabilities
with supervised learning. *Proceedings of ICML*, 625-632.
<https://doi.org/10.1145/1102351.1102430>

Thompson, W. R. (1933). On the likelihood that one unknown probability
exceeds another in view of the evidence of two samples.
*Biometrika*, 25(3-4), 285-294.
<https://doi.org/10.1093/biomet/25.3-4.285>

Russo, D. J., Van Roy, B., Kazerouni, A., Osband, I., & Wen, Z. (2018).
A tutorial on Thompson sampling.
*Foundations and Trends in Machine Learning*, 11(1), 1-96.
<https://doi.org/10.1561/2200000070>

## Causality and policy evaluation {.unnumbered}

Dudík, M., Langford, J., & Li, L. (2011).
Doubly robust policy evaluation and learning.
*Proceedings of ICML*, 1097-1104.
<https://arxiv.org/abs/1103.4601>

Künzel, S. R., Sekhon, J. S., Bickel, P. J., & Yu, B. (2019).
Metalearners for estimating heterogeneous treatment effects using
machine learning. *PNAS*, 116(10), 4156-4165.
<https://doi.org/10.1073/pnas.1804597116>

Wager, S., & Athey, S. (2018).
Estimation and inference of heterogeneous treatment effects using
random forests. *Journal of the American Statistical Association*,
113(523), 1228-1242.
<https://doi.org/10.1080/01621459.2017.1319839>

Thomas, P., & Brunskill, E. (2016).
Data-efficient off-policy policy evaluation for reinforcement learning.
*Proceedings of ICML*, 2139-2148.
<https://proceedings.mlr.press/v48/thomasa16.html>

## Regulations and publishing tools {.unnumbered}

Regulation (EU) 2016/679. General Data Protection Regulation.
<https://eur-lex.europa.eu/eli/reg/2016/679/oj>

Regulation (EU) 2024/1689. Artificial Intelligence Act.
<https://eur-lex.europa.eu/eli/reg/2024/1689/oj>

Pandoc. *User's Guide*: metadata, tables of contents, section numbering,
and PDF creation. <https://pandoc.org/MANUAL.html>

## Editorial note {.unnumbered}

The book develops and reorganizes the original paper with AI-assisted
editing. The English edition adapts terminology and sentence structure
for technical accuracy and plain-language readability.
It adds explicit distinctions where literal translation would be
misleading, including geometric orthogonality versus statistical
independence, participant confidence versus model uncertainty, and
scores versus probabilities.

The learning-platform illustration is reused from the source assets;
its labels are in English. The human-development flow is rendered as
an English table so Vietnamese labels are not left inside an image.
The author remains responsible for final content, asset rights, and
interpretation before formal publication.
