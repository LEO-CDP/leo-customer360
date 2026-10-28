(function (root) {
  "use strict";

  const Handlebars = root.Handlebars;
  if (!Handlebars) throw new Error("Workflow templates require Handlebars.");

  Handlebars.registerPartial(
    "workflowConfigurationField",
    `
      {{#if isSegment}}
        <div class="segment-readonly" data-k="{{key}}" data-value="{{value}}" role="group" aria-label="Customer segment: {{segmentName}}">
          <span class="segment-readonly-icon" aria-hidden="true">✦</span>
          <span class="segment-readonly-copy">
            <small>{{label}}</small>
            <strong>{{segmentName}}</strong>
          </span>
          <span class="segment-readonly-status">Connected</span>
        </div>
      {{else}}
        <label class="fl">{{label}}</label>
        {{#if isEnum}}
          <select data-k="{{key}}">
            {{#each options}}
              <option value="{{value}}" {{#if selected}}selected{{/if}}>{{label}}</option>
            {{/each}}
          </select>
        {{else if isLongText}}
          <textarea data-k="{{key}}">{{value}}</textarea>
        {{else}}
          <input data-k="{{key}}" type="{{inputType}}" value="{{value}}">
        {{/if}}
      {{/if}}
    `,
  );
  Handlebars.registerPartial(
    "workflowBuildGuide",
    `
      <section class="build-guide" aria-labelledby="build-guide-title">
        <span class="build-guide-icon" aria-hidden="true">1</span>
        <div class="build-guide-content">
          <p class="build-guide-eyebrow">{{#if hasSegment}}AUDIENCE{{else}}START HERE{{/if}}</p>
          <h3 id="build-guide-title">{{#if hasSegment}}Add your first action{{else}}Select a customer segment{{/if}}</h3>
          <p>
            {{#if loading}}
              Loading profiles for <strong>{{segmentName}}</strong>…
            {{else if hasAudience}}
              {{profileCount}} profiles from <strong>{{segmentName}}</strong> are ready. Choose an action to start the journey.
            {{else if hasSegment}}
              No profiles were returned for <strong>{{segmentName}}</strong>. You can still build the journey, but cannot run it yet.
            {{else}}
              Choose an audience in the campaign plan to load the profiles who will enter this journey.
            {{/if}}
          </p>
          {{#if canChooseAction}}
            <div class="build-guide-actions" role="group" aria-label="Add your first message">
              {{#each actions}}
                <button type="button" class="build-action {{className}}" data-add-action="{{type}}" {{#unless canAdd}}disabled{{/unless}}>
                  <span aria-hidden="true">{{icon}}</span>{{label}}
                </button>
              {{/each}}
            </div>
          {{/if}}
        </div>
      </section>
    `,
  );

  const WorkflowCore = root.WorkflowCore;
  if (!WorkflowCore)
    throw new Error("Workflow templates require WorkflowCore.");

  const node = (id, type, config = {}, children = []) => ({
    id,
    type,
    config: { ...WorkflowCore.defaults(type), ...config },
    children,
  });
  const branch = (name, child) => ({ ...child, branch: name });
  const goal = (id) => node(id, "exit", { kind: "goal" });
  const exit = (id) => node(id, "exit", { kind: "exit" });
  const recipe = (industry, name, intent, rootNode) => ({
    industry,
    name,
    intent,
    workflow: { schema_version: 2, root: rootNode },
  });

  const RECIPES = {
    welcome: recipe(
      "Retail & E-Commerce",
      "Welcome series",
      "Welcome new sign-ups and follow up only when they have not engaged.",
      node("n1", "trigger", { event: "signup" }, [
        node(
          "n2",
          "email",
          {
            subject: "Welcome, {{first_name}}",
            body: "Here is how to get started.",
          },
          [
            node(
              "n3",
              "wait_event",
              {
                event: "email_opened",
                amount: 2,
                unit: "days",
              },
              [
                branch(
                  "happened",
                  node(
                    "n4",
                    "update_profile",
                    {
                      action: "add_tag",
                      tag: "welcome_engaged",
                    },
                    [goal("n5")],
                  ),
                ),
                branch(
                  "timeout",
                  node(
                    "n6",
                    "notification",
                    {
                      body: "{{first_name}}, your welcome gift is waiting.",
                    },
                    [exit("n7")],
                  ),
                ),
              ],
            ),
          ],
        ),
      ]),
    ),
    cart_abandonment: recipe(
      "Retail & E-Commerce",
      "Cart abandonment",
      "Recover abandoned carts with a relevant message and a purchase-aware follow-up.",
      node("n1", "trigger", { event: "cart_abandoned" }, [
        node("n2", "wait", { amount: 1, unit: "hours" }, [
          node(
            "n3",
            "condition",
            {
              field: "historical_clv",
              op: "ge",
              value: "150",
            },
            [
              branch(
                "yes",
                node(
                  "n4",
                  "email",
                  {
                    subject: "{{first_name}}, your cart is waiting",
                    body: "Your saved picks are waiting. Come back when you are ready.",
                  },
                  [
                    node(
                      "n5",
                      "wait_event",
                      {
                        event: "purchase",
                        amount: 2,
                        unit: "days",
                      },
                      [
                        branch("happened", goal("n6")),
                        branch(
                          "timeout",
                          node(
                            "n7",
                            "push",
                            {
                              title: "Still thinking it over?",
                              body: "{{first_name}}, your saved cart is one tap away.",
                            },
                            [exit("n8")],
                          ),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
              branch(
                "no",
                node(
                  "n9",
                  "push",
                  {
                    title: "Your cart is saved",
                    body: "{{first_name}}, come back to your items whenever you are ready.",
                  },
                  [
                    node(
                      "n10",
                      "wait_event",
                      {
                        event: "purchase",
                        amount: 2,
                        unit: "days",
                      },
                      [
                        branch("happened", goal("n11")),
                        branch("timeout", exit("n12")),
                      ],
                    ),
                  ],
                ),
              ),
            ],
          ),
        ]),
      ]),
    ),
    winback: recipe(
      "Retail & E-Commerce",
      "Win-back",
      "Re-engage inactive customers and stop follow-ups after a purchase.",
      node("n1", "trigger", { event: "inactive_30d" }, [
        node(
          "n2",
          "email",
          {
            subject: "We miss you, {{first_name}}",
            body: "Take a look at what is new since your last visit.",
          },
          [
            node(
              "n3",
              "wait_event",
              {
                event: "purchase",
                amount: 7,
                unit: "days",
              },
              [
                branch("happened", goal("n4")),
                branch(
                  "timeout",
                  node(
                    "n5",
                    "notification",
                    {
                      body: "{{first_name}}, we would love to welcome you back.",
                    },
                    [
                      node(
                        "n6",
                        "update_profile",
                        {
                          action: "add_tag",
                          tag: "winback_unresponsive",
                        },
                        [exit("n7")],
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ],
        ),
      ]),
    ),
    post_purchase: recipe(
      "Retail & E-Commerce",
      "Post-purchase care",
      "Thank customers after an order and tailor follow-up to their engagement.",
      node("n1", "trigger", { event: "purchase" }, [
        node("n2", "wait", { amount: 3, unit: "days" }, [
          node(
            "n3",
            "email",
            {
              subject: "How was your order, {{first_name}}?",
              body: "We hope everything arrived safely. Tell us what you think.",
            },
            [
              node("n4", "wait", { amount: 4, unit: "days" }, [
                node(
                  "n5",
                  "condition",
                  {
                    field: "engagement_score",
                    op: "ge",
                    value: "60",
                  },
                  [
                    branch(
                      "yes",
                      node(
                        "n6",
                        "update_profile",
                        {
                          action: "add_tag",
                          tag: "advocate",
                        },
                        [goal("n7")],
                      ),
                    ),
                    branch(
                      "no",
                      node(
                        "n8",
                        "push",
                        {
                          title: "Need help with your order?",
                          body: "We are here if anything is off, {{first_name}}.",
                        },
                        [exit("n9")],
                      ),
                    ),
                  ],
                ),
              ]),
            ],
          ),
        ]),
      ]),
    ),
    travel_preparation: recipe(
      "Travel & Hospitality",
      "Trip preparation",
      "Send useful trip information and check whether travelers have seen it.",
      node("n1", "trigger", { event: "purchase" }, [
        node("n2", "wait", { amount: 7, unit: "days" }, [
          node(
            "n3",
            "email",
            {
              subject: "Your trip is coming up, {{first_name}}",
              body: "Review your itinerary and travel details before departure.",
            },
            [
              node(
                "n4",
                "wait_event",
                {
                  event: "email_opened",
                  amount: 2,
                  unit: "days",
                },
                [
                  branch(
                    "happened",
                    node(
                      "n5",
                      "update_profile",
                      {
                        action: "add_tag",
                        tag: "travel_info_viewed",
                      },
                      [goal("n6")],
                    ),
                  ),
                  branch(
                    "timeout",
                    node(
                      "n7",
                      "notification",
                      {
                        body: "{{first_name}}, your trip details are ready to review.",
                      },
                      [exit("n8")],
                    ),
                  ),
                ],
              ),
            ],
          ),
        ]),
      ]),
    ),
    course_enrollment: recipe(
      "Education",
      "Course enrollment",
      "Welcome learners, then guide them toward their first course resource.",
      node("n1", "trigger", { event: "signup" }, [
        node(
          "n2",
          "email",
          {
            subject: "Your learning journey starts here, {{first_name}}",
            body: "Open your course guide and choose a good time for your first lesson.",
          },
          [
            node(
              "n3",
              "wait_event",
              {
                event: "email_clicked",
                amount: 3,
                unit: "days",
              },
              [
                branch(
                  "happened",
                  node(
                    "n4",
                    "update_profile",
                    {
                      action: "add_tag",
                      tag: "course_engaged",
                    },
                    [goal("n5")],
                  ),
                ),
                branch(
                  "timeout",
                  node(
                    "n6",
                    "notification",
                    {
                      body: "{{first_name}}, your course guide is ready when you are.",
                    },
                    [exit("n7")],
                  ),
                ),
              ],
            ),
          ],
        ),
      ]),
    ),
    replenishment_reminder: recipe(
      "Retail & E-Commerce",
      "Replenishment reminder",
      "Remind repeat-purchase customers when it may be time to restock.",
      node("n1", "trigger", { event: "purchase" }, [
        node("n2", "wait", { amount: 30, unit: "days" }, [
          node(
            "n3",
            "email",
            {
              subject: "Ready for a restock, {{first_name}}?",
              body: "If you are running low, your usual items are ready to reorder.",
            },
            [
              node(
                "n4",
                "wait_event",
                {
                  event: "purchase",
                  amount: 7,
                  unit: "days",
                },
                [
                  branch("happened", goal("n5")),
                  branch(
                    "timeout",
                    node(
                      "n6",
                      "push",
                      {
                        title: "Need a restock?",
                        body: "{{first_name}}, your favorites are easy to reorder.",
                      },
                      [exit("n7")],
                    ),
                  ),
                ],
              ),
            ],
          ),
        ]),
      ]),
    ),
    retail_loyalty: recipe(
      "Retail & E-Commerce",
      "Loyalty member activation",
      "Recognize loyalty members and invite other shoppers to join the program.",
      node("n1", "trigger", { event: "purchase" }, [
        node(
          "n2",
          "condition",
          {
            field: "segmentation_tags",
            op: "has",
            value: "loyalty_member",
          },
          [
            branch(
              "yes",
              node(
                "n3",
                "push",
                {
                  title: "A thank-you for being a member",
                  body: "{{first_name}}, take a look at your latest member rewards.",
                },
                [goal("n4")],
              ),
            ),
            branch(
              "no",
              node(
                "n5",
                "email",
                {
                  subject: "Get more from your next visit, {{first_name}}",
                  body: "Join our loyalty program to hear about member benefits.",
                },
                [
                  node(
                    "n6",
                    "update_profile",
                    {
                      action: "add_tag",
                      tag: "loyalty_invited",
                    },
                    [exit("n7")],
                  ),
                ],
              ),
            ),
          ],
        ),
      ]),
    ),
    restaurant_feedback: recipe(
      "Hospitality & Food Service",
      "Dining follow-up",
      "Thank guests after a visit and offer a second feedback route if needed.",
      node("n1", "trigger", { event: "purchase" }, [
        node("n2", "wait", { amount: 1, unit: "days" }, [
          node(
            "n3",
            "email",
            {
              subject: "Thanks for visiting, {{first_name}}",
              body: "We hope you enjoyed your meal. We would value your feedback.",
            },
            [
              node(
                "n4",
                "wait_event",
                {
                  event: "email_clicked",
                  amount: 3,
                  unit: "days",
                },
                [
                  branch(
                    "happened",
                    node(
                      "n5",
                      "update_profile",
                      {
                        action: "add_tag",
                        tag: "restaurant_feedback",
                      },
                      [goal("n6")],
                    ),
                  ),
                  branch(
                    "timeout",
                    node(
                      "n7",
                      "notification",
                      {
                        body: "{{first_name}}, how was your recent dining experience?",
                      },
                      [exit("n8")],
                    ),
                  ),
                ],
              ),
            ],
          ),
        ]),
      ]),
    ),
    hospitality_prearrival: recipe(
      "Travel & Hospitality",
      "Pre-arrival guest journey",
      "Share arrival information before a stay and check whether guests have seen it.",
      node("n1", "trigger", { event: "purchase" }, [
        node("n2", "wait", { amount: 7, unit: "days" }, [
          node(
            "n3",
            "email",
            {
              subject: "We look forward to welcoming you, {{first_name}}",
              body: "Your stay is coming up. Review arrival details and let us know if you need help.",
            },
            [
              node(
                "n4",
                "wait_event",
                {
                  event: "email_opened",
                  amount: 2,
                  unit: "days",
                },
                [
                  branch(
                    "happened",
                    node(
                      "n5",
                      "update_profile",
                      {
                        action: "add_tag",
                        tag: "stay_prepared",
                      },
                      [goal("n6")],
                    ),
                  ),
                  branch(
                    "timeout",
                    node(
                      "n7",
                      "notification",
                      {
                        body: "{{first_name}}, reply if you need help getting ready for your stay.",
                      },
                      [exit("n8")],
                    ),
                  ),
                ],
              ),
            ],
          ),
        ]),
      ]),
    ),
    account_opening: recipe(
      "Banking & Financial Services",
      "Account opening follow-up",
      "Help new applicants finish opening an account, then tag those who start setup.",
      node("n1", "trigger", { event: "signup" }, [
        node(
          "n2",
          "email",
          {
            subject: "Finish opening your account, {{first_name}}",
            body: "Your application is almost complete. Pick up where you left off.",
          },
          [
            node(
              "n3",
              "wait_event",
              { event: "email_clicked", amount: 3, unit: "days" },
              [
                branch(
                  "happened",
                  node(
                    "n4",
                    "update_profile",
                    { action: "add_tag", tag: "account_setup_started" },
                    [goal("n5")],
                  ),
                ),
                branch(
                  "timeout",
                  node(
                    "n6",
                    "notification",
                    {
                      body: "{{first_name}}, your account is waiting. Finish in a few minutes.",
                    },
                    [exit("n7")],
                  ),
                ),
              ],
            ),
          ],
        ),
      ]),
    ),
    card_activation: recipe(
      "Banking & Financial Services",
      "Card activation reminder",
      "Remind new cardholders to activate their card, with a stronger nudge for engaged customers.",
      node("n1", "trigger", { event: "purchase" }, [
        node("n2", "wait", { amount: 2, unit: "days" }, [
          node("n3", "condition", { field: "engagement_score", op: "ge", value: "60" }, [
            branch(
              "yes",
              node(
                "n4",
                "push",
                {
                  title: "Activate your card",
                  body: "{{first_name}}, one tap to start using your new card.",
                },
                [
                  node(
                    "n5",
                    "wait_event",
                    { event: "purchase", amount: 7, unit: "days" },
                    [
                      branch("happened", goal("n6")),
                      branch("timeout", exit("n7")),
                    ],
                  ),
                ],
              ),
            ),
            branch(
              "no",
              node(
                "n8",
                "email",
                {
                  subject: "Activate your card to start earning rewards",
                  body: "Activate your card in the app to start using it today.",
                },
                [exit("n9")],
              ),
            ),
          ]),
        ]),
      ]),
    ),
    course_reengagement: recipe(
      "Education",
      "Course re-engagement",
      "Bring back learners who stopped partway through a course.",
      node("n1", "trigger", { event: "inactive_30d" }, [
        node(
          "n2",
          "email",
          {
            subject: "Your course is waiting, {{first_name}}",
            body: "Pick up your next lesson where you stopped.",
          },
          [
            node(
              "n3",
              "wait_event",
              { event: "email_opened", amount: 3, unit: "days" },
              [
                branch(
                  "happened",
                  node(
                    "n4",
                    "update_profile",
                    { action: "add_tag", tag: "course_reengaged" },
                    [goal("n5")],
                  ),
                ),
                branch(
                  "timeout",
                  node(
                    "n6",
                    "push",
                    {
                      title: "Your next lesson is ready",
                      body: "{{first_name}}, a short lesson is ready when you are.",
                    },
                    [exit("n7")],
                  ),
                ),
              ],
            ),
          ],
        ),
      ]),
    ),
    appointment_reminder: recipe(
      "Healthcare",
      "Check-up reminder",
      "Remind patients when a routine check-up is due and make booking easy.",
      node("n1", "trigger", { event: "purchase" }, [
        node("n2", "wait", { amount: 7, unit: "days" }, [
          node(
            "n3",
            "email",
            {
              subject: "Your routine check-up is due, {{first_name}}",
              body: "Choose a time that suits you. Use the app or reply to schedule.",
            },
            [
              node(
                "n4",
                "wait_event",
                { event: "email_clicked", amount: 3, unit: "days" },
                [
                  branch(
                    "happened",
                    node(
                      "n5",
                      "update_profile",
                      { action: "add_tag", tag: "checkup_scheduling" },
                      [goal("n6")],
                    ),
                  ),
                  branch(
                    "timeout",
                    node(
                      "n7",
                      "notification",
                      {
                        body: "{{first_name}}, book your check-up in two taps.",
                      },
                      [exit("n8")],
                    ),
                  ),
                ],
              ),
            ],
          ),
        ]),
      ]),
    ),
    post_visit_care: recipe(
      "Healthcare",
      "Post-visit follow-up",
      "Check in after a visit and route patients who need more support to a care contact.",
      node("n1", "trigger", { event: "purchase" }, [
        node("n2", "wait", { amount: 1, unit: "days" }, [
          node(
            "n3",
            "email",
            {
              subject: "How are you feeling, {{first_name}}?",
              body: "If anything is unclear after your visit, we are here to help.",
            },
            [
              node(
                "n4",
                "condition",
                { field: "engagement_score", op: "ge", value: "60" },
                [
                  branch(
                    "yes",
                    node(
                      "n5",
                      "update_profile",
                      { action: "add_tag", tag: "care_plan_engaged" },
                      [goal("n6")],
                    ),
                  ),
                  branch(
                    "no",
                    node(
                      "n7",
                      "notification",
                      {
                        body: "{{first_name}}, reply if you would like a call from our care team.",
                      },
                      [exit("n8")],
                    ),
                  ),
                ],
              ),
            ],
          ),
        ]),
      ]),
    ),
    table_reservation: recipe(
      "Hospitality & Food Service",
      "Table reservation reminder",
      "Confirm an upcoming booking, then check whether the guest returns.",
      node("n1", "trigger", { event: "purchase" }, [
        node("n2", "wait", { amount: 1, unit: "days" }, [
          node(
            "n3",
            "notification",
            {
              body: "{{first_name}}, your table is booked for tomorrow. Reply to change it.",
            },
            [
              node(
                "n4",
                "wait_event",
                { event: "purchase", amount: 2, unit: "days" },
                [branch("happened", goal("n5")), branch("timeout", exit("n6"))],
              ),
            ],
          ),
        ]),
      ]),
    ),
    policy_renewal: recipe(
      "Insurance",
      "Policy renewal reminder",
      "Remind policyholders before renewal, then follow up by SMS if they have not renewed.",
      node("n1", "trigger", { event: "purchase" }, [
        node("n2", "wait", { amount: 30, unit: "days" }, [
          node(
            "n3",
            "email",
            {
              subject: "Your policy renews soon, {{first_name}}",
              body: "Review your cover and renew in a few minutes.",
            },
            [
              node(
                "n4",
                "wait_event",
                { event: "email_clicked", amount: 14, unit: "days" },
                [
                  branch("happened", goal("n5")),
                  branch(
                    "timeout",
                    node(
                      "n6",
                      "notification",
                      {
                        body: "{{first_name}}, renew your policy to keep your cover active.",
                      },
                      [exit("n7")],
                    ),
                  ),
                ],
              ),
            ],
          ),
        ]),
      ]),
    ),
    quote_followup: recipe(
      "Insurance",
      "Quote follow-up",
      "Finish unfinished quotes. Higher-scoring prospects get an email, others get a push reminder.",
      node("n1", "trigger", { event: "cart_abandoned" }, [
        node("n2", "wait", { amount: 1, unit: "hours" }, [
          node("n3", "condition", { field: "engagement_score", op: "ge", value: "50" }, [
            branch(
              "yes",
              node(
                "n4",
                "email",
                {
                  subject: "{{first_name}}, finish your quote",
                  body: "Your quote is saved. Complete it in a few minutes.",
                },
                [
                  node(
                    "n5",
                    "wait_event",
                    { event: "purchase", amount: 3, unit: "days" },
                    [
                      branch("happened", goal("n6")),
                      branch("timeout", exit("n7")),
                    ],
                  ),
                ],
              ),
            ),
            branch(
              "no",
              node(
                "n8",
                "push",
                {
                  title: "Your quote is saved",
                  body: "{{first_name}}, pick up where you left off.",
                },
                [exit("n9")],
              ),
            ),
          ]),
        ]),
      ]),
    ),
    b2b_reorder: recipe(
      "Manufacturing",
      "Supply reorder reminder",
      "Remind trade customers when a reorder is due, then escalate by push if they have not ordered.",
      node("n1", "trigger", { event: "purchase" }, [
        node("n2", "wait", { amount: 60, unit: "days" }, [
          node(
            "n3",
            "email",
            {
              subject: "Time to reorder supplies, {{first_name}}",
              body: "Your usual order is ready to confirm. Reorder in one step.",
            },
            [
              node(
                "n4",
                "wait_event",
                { event: "purchase", amount: 7, unit: "days" },
                [
                  branch("happened", goal("n5")),
                  branch(
                    "timeout",
                    node(
                      "n6",
                      "push",
                      {
                        title: "Reorder reminder",
                        body: "{{first_name}}, your supply order is ready to confirm.",
                      },
                      [exit("n7")],
                    ),
                  ),
                ],
              ),
            ],
          ),
        ]),
      ]),
    ),
    product_registration: recipe(
      "Manufacturing",
      "Product registration",
      "Ask new buyers to register their product for warranty support, then nudge by SMS.",
      node("n1", "trigger", { event: "signup" }, [
        node(
          "n2",
          "email",
          {
            subject: "Register your product, {{first_name}}",
            body: "Registering takes two minutes and activates your warranty.",
          },
          [
            node(
              "n3",
              "wait_event",
              { event: "email_clicked", amount: 7, unit: "days" },
              [
                branch(
                  "happened",
                  node(
                    "n4",
                    "update_profile",
                    { action: "add_tag", tag: "product_registered" },
                    [goal("n5")],
                  ),
                ),
                branch(
                  "timeout",
                  node(
                    "n6",
                    "notification",
                    {
                      body: "{{first_name}}, register your product to activate your warranty.",
                    },
                    [exit("n7")],
                  ),
                ),
              ],
            ),
          ],
        ),
      ]),
    ),
    trial_activation: recipe(
      "Media & Entertainment",
      "Trial activation",
      "Welcome new subscribers and recommend a first title. Engaged trials get a push.",
      node("n1", "trigger", { event: "signup" }, [
        node(
          "n2",
          "email",
          {
            subject: "Your trial has started, {{first_name}}",
            body: "Here is how to find something to watch tonight.",
          },
          [
            node("n3", "wait", { amount: 1, unit: "days" }, [
              node(
                "n4",
                "condition",
                { field: "engagement_score", op: "ge", value: "60" },
                [
                  branch(
                    "yes",
                    node(
                      "n5",
                      "push",
                      {
                        title: "Pick your first show",
                        body: "{{first_name}}, new titles are waiting for you.",
                      },
                      [
                        node(
                          "n6",
                          "wait_event",
                          { event: "purchase", amount: 7, unit: "days" },
                          [
                            branch("happened", goal("n7")),
                            branch("timeout", exit("n8")),
                          ],
                        ),
                      ],
                    ),
                  ),
                  branch(
                    "no",
                    node(
                      "n9",
                      "email",
                      {
                        subject: "Here is what to watch next",
                        body: "Browse our top picks for new subscribers.",
                      },
                      [exit("n10")],
                    ),
                  ),
                ],
              ),
            ]),
          ],
        ),
      ]),
    ),
    lapsed_viewer: recipe(
      "Media & Entertainment",
      "Lapsed viewer win-back",
      "Bring back viewers who have been inactive for 30 days with new releases, then offer a text reminder.",
      node("n1", "trigger", { event: "inactive_30d" }, [
        node(
          "n2",
          "push",
          {
            title: "New releases you may like",
            body: "{{first_name}}, fresh titles just landed.",
          },
          [
            node(
              "n3",
              "wait_event",
              { event: "purchase", amount: 7, unit: "days" },
              [
                branch("happened", goal("n4")),
                branch(
                  "timeout",
                  node(
                    "n5",
                    "notification",
                    { body: "{{first_name}}, a new season is ready to watch." },
                    [
                      node(
                        "n6",
                        "update_profile",
                        { action: "add_tag", tag: "lapsed_viewer" },
                        [exit("n7")],
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ],
        ),
      ]),
    ),
    new_listing_alert: recipe(
      "Real Estate",
      "New listing alert",
      "Send buyers new homes that match their search, then follow up by SMS if they do not engage.",
      node("n1", "trigger", { event: "signup" }, [
        node(
          "n2",
          "email",
          {
            subject: "New homes that match your search, {{first_name}}",
            body: "Here are the newest listings in your preferred area.",
          },
          [
            node(
              "n3",
              "wait_event",
              { event: "email_clicked", amount: 3, unit: "days" },
              [
                branch(
                  "happened",
                  node(
                    "n4",
                    "update_profile",
                    { action: "add_tag", tag: "listing_viewed" },
                    [goal("n5")],
                  ),
                ),
                branch(
                  "timeout",
                  node(
                    "n6",
                    "notification",
                    {
                      body: "{{first_name}}, new listings are live. Take a look.",
                    },
                    [exit("n7")],
                  ),
                ),
              ],
            ),
          ],
        ),
      ]),
    ),
    viewing_followup: recipe(
      "Real Estate",
      "Viewing follow-up",
      "Follow up with buyers who looked at a home but did not book a second viewing.",
      node("n1", "trigger", { event: "cart_abandoned" }, [
        node("n2", "wait", { amount: 1, unit: "days" }, [
          node(
            "n3",
            "email",
            {
              subject: "Still interested in this home, {{first_name}}?",
              body: "Book a second viewing at a time that works for you.",
            },
            [
              node(
                "n4",
                "wait_event",
                { event: "purchase", amount: 14, unit: "days" },
                [
                  branch("happened", goal("n5")),
                  branch(
                    "timeout",
                    node(
                      "n6",
                      "push",
                      {
                        title: "Book a second viewing",
                        body: "{{first_name}}, a slot is open this week.",
                      },
                      [exit("n7")],
                    ),
                  ),
                ],
              ),
            ],
          ),
        ]),
      ]),
    ),
    plan_upgrade: recipe(
      "Telecommunications",
      "Plan upgrade offer",
      "Offer a higher data plan to high-engagement customers and share plan details with others.",
      node("n1", "trigger", { event: "purchase" }, [
        node("n2", "wait", { amount: 14, unit: "days" }, [
          node("n3", "condition", { field: "engagement_score", op: "ge", value: "70" }, [
            branch(
              "yes",
              node(
                "n4",
                "push",
                {
                  title: "Upgrade for more data",
                  body: "{{first_name}}, a bigger plan is available for you.",
                },
                [
                  node(
                    "n5",
                    "wait_event",
                    { event: "purchase", amount: 7, unit: "days" },
                    [
                      branch("happened", goal("n6")),
                      branch("timeout", exit("n7")),
                    ],
                  ),
                ],
              ),
            ),
            branch(
              "no",
              node(
                "n8",
                "email",
                {
                  subject: "Your plan at a glance",
                  body: "Review your data, calls and included extras.",
                },
                [exit("n9")],
              ),
            ),
          ]),
        ]),
      ]),
    ),
    line_activation: recipe(
      "Telecommunications",
      "Line activation",
      "Help new subscribers activate their line by SMS, then tag those who have not activated.",
      node("n1", "trigger", { event: "signup" }, [
        node(
          "n2",
          "notification",
          { body: "{{first_name}}, activate your line in two minutes." },
          [
            node(
              "n3",
              "wait_event",
              { event: "purchase", amount: 3, unit: "days" },
              [
                branch("happened", goal("n4")),
                branch(
                  "timeout",
                  node(
                    "n5",
                    "update_profile",
                    { action: "add_tag", tag: "line_not_activated" },
                    [exit("n6")],
                  ),
                ),
              ],
            ),
          ],
        ),
      ]),
    ),
  };

  const source = {
    workflowPalette: `
      {{#each categories}}
        <h2>{{label}}</h2>
        {{#each nodes}}
          <div class="blockelem create-flowy t-{{type}}" data-type="{{type}}">
            <input type="hidden" name="blockelemtype" class="blockelemtype" value="{{type}}">
            <b>{{label}}</b>
            <span class="d">{{description}}</span>
          </div>
        {{/each}}
      {{/each}}
      <p>The audience is linked to this campaign. Drag blocks below the Event trigger. Branching blocks place the first branch left and the second right. Templates support {{templateVariables}}.</p>
    `,
    workflowBlock: `
      <input type="hidden" name="blockelemtype" class="blockelemtype" value="{{type}}">
      <input type="hidden" name="blockid" class="blockid" value="{{blockId}}">
      <div class="bh">
        <span class="block-title">{{label}}</span>
        <span class="hits"></span>
        <span class="branch"></span>
      </div>
      {{#each fields}}
        {{> workflowConfigurationField}}
      {{/each}}
    `,
    workflowProfiles: `
      {{#if showBuildGuide}}{{> workflowBuildGuide}}{{/if}}
      <h2>Audience profiles</h2>
      <p class="sub">{{#if loading}}Loading profiles…{{else}}{{profileCount}} profiles. {{segmentHint}}{{/if}}</p>
      <table>
        <tr>
          <th>Name</th><th>Engagement</th><th>Churn</th><th>CLV</th><th>Push</th>
          {{#if hasSimulation}}<th>Simulated outcome</th><th>Virtual time</th>{{/if}}
        </tr>
        {{#each profiles}}
          <tr>
            <td>{{name}}</td><td>{{engagement_score}}</td><td>{{churn_probability}}</td><td>&#36;{{historical_clv}}</td><td>{{pushLabel}}</td>
            {{#if ../hasSimulation}}<td>{{outcome}}</td><td>{{days}}d</td>{{/if}}
          </tr>
        {{/each}}
      </table>
    `,
    workflowChecks: `
      <h2>Checks</h2>
      {{#if showBuildGuide}}
        {{> workflowBuildGuide}}
      {{/if}}
      {{#if issues.length}}
        <ul class="issues">
          {{#each issues}}
            <li class="{{level}}">
              <b>{{msg}}</b>{{#if node}} <small>({{node}})</small>{{/if}}
              {{#if fix}}<br><small>{{fix}}</small>{{/if}}
            </li>
          {{/each}}
        </ul>
      {{else if showBuildGuide}}
        <div class="empty">Add an action, then use Check Flow to validate and simulate the journey.</div>
      {{else}}
        <div class="empty">No issues. Select Check Flow to preview simulated outcomes. No messages will be sent.</div>
      {{/if}}
    `,
    workflowReportEmpty: `
      <div class="simulation-notice"><strong>Simulation preview</strong><p>Use Check Flow to validate the journey and estimate outcomes for the loaded audience sample. Nothing is sent, synced, or saved to customer profiles.</p></div>
      <div class="empty">No simulation yet. Select Check Flow to preview the journey using virtual time.</div>
    `,
    workflowReport: `
      <div class="simulation-notice" role="note"><span class="simulation-badge">SIMULATION ONLY</span><h2>Flow simulation</h2><p>Estimated outcomes, not live campaign performance. No messages sent, audiences synced, or customer profiles changed. Delays advance virtual time.</p></div>
      <p class="sub">Preview for {{totals.entered}} loaded profiles, not the entire campaign audience. {{totals.goal}} reached a goal node, {{totals.exited}} exited, {{totals.ended}} ended without an exit. Engagement, conversions and revenue are modeled estimates, not measured results or forecasts.</p>
      <div class="kpis">
        {{#each kpis}}
          <div class="kpi"><div class="num">{{value}}</div><small>{{label}}</small></div>
        {{/each}}
      </div>
      <div class="fun">
        {{#each funnel}}
          <div><span>{{label}}</span><i style="width:{{width}}%"></i><span>{{value}}</span></div>
        {{/each}}
      </div>
      <h2 class="message-report-heading">Simulated message outcomes</h2>
      {{#each messages}}
        <div class="ncard t-{{type}}">
          <b>{{label}}: {{title}}</b>
          <div class="m">
            <span>{{reach}} sample profiles</span><span>{{delivered}} simulated deliveries</span>
            {{#unless isSms}}<span>{{openRate}} modeled opens</span>{{/unless}}
            <span>{{clickRate}} modeled clicks</span><span>{{conversions}} simulated conversions</span><span>&#36;{{revenue}} modeled value</span>
          </div>
          <p>{{body}}</p>
        </div>
      {{/each}}
    `,
    workflowShell: `
      <header>
        <h1>{{title}}</h1>
        <span id="pill" class="pill ok">Ready to check</span>
        <span class="simulation-badge" title="Design and local simulation only. Activate the approved campaign from Campaign details for real execution.">Simulation mode</span>
        <select id="recipe" aria-label="Load a recipe">
          <option value="">Load recipe…</option>
          {{#each recipeGroups}}
            <optgroup label="{{industry}}">
              {{#each recipes}}<option value="{{key}}">{{name}}</option>{{/each}}
            </optgroup>
          {{/each}}
        </select>
        <button id="bClear">Clear canvas</button>
        <button id="bRun" type="button" class="primary" title="Validate and simulate this flow. No real campaign is started.">Check Flow</button>
      </header>
      <main>
        <aside class="pal" id="pal"></aside>
        <div id="wrap">
          <div class="canvas-controls" role="group" aria-label="Canvas navigation">
            <button type="button" id="bPanCanvas" aria-pressed="false" title="Enable pan mode, then drag the empty canvas">Pan canvas</button>
            <button type="button" id="bZoomOut" aria-label="Zoom out" title="Zoom out">−</button>
            <span id="canvasZoomLevel" aria-live="polite">100%</span>
            <button type="button" id="bZoomIn" aria-label="Zoom in" title="Zoom in">+</button>
            <button type="button" id="bResetCanvasView" title="Reset zoom and return to the top-left of the canvas">Reset view</button>
          </div>
          <div id="canvas"></div>
        </div>
        <section class="side">
          <div class="tabs" role="tablist">
            <button role="tab" id="tabSeg" aria-selected="true">Profiles</button>
            <button role="tab" id="tabChk" aria-selected="false">Checks</button>
            <button role="tab" id="tabRep" aria-selected="false">Simulation</button>
            <button role="tab" id="tabJson" aria-selected="false">JSON</button>
          </div>
          <div class="pane" id="paneSeg"><div id="segTable"></div></div>
          <div class="pane" id="paneChk" hidden></div>
          <div class="pane" id="paneRep" hidden></div>
          <div class="pane" id="paneJson" hidden>
            <h2>Workflow JSON</h2>
            <p class="sub">The same JSON an AI agent reads and writes. Edit it and apply to rebuild the canvas.</p>
            <textarea id="jsonData" spellcheck="false"></textarea>
            <div class="json-actions"><button id="bApply">Apply JSON</button><button id="bCopyJson">Copy</button></div>
            <details>
              <summary>Agent manifest</summary>
              <p class="sub">Node catalog, rules, tools and example recipes for an agent prompt. Also available as <code>workflowAgent.manifest()</code>.</p>
              <button id="bCopyManifest">Copy manifest</button>
            </details>
          </div>
        </section>
      </main>
      <div id="toast" role="status"></div>
    `,
  };

  const templates = Object.fromEntries(
    Object.entries(source).map(([name, markup]) => [
      name,
      Handlebars.compile(markup),
    ]),
  );
  templates.RECIPES = RECIPES;
  root.WorkflowTemplates = templates;
})(typeof self !== "undefined" ? self : this);
