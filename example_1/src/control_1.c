/* Includes_BEGIN */
#ifdef MATLAB_MEX_FILE
  #include "tmwtypes.h"   /* defines real_T for MEX builds */
#else
  #include "rtwtypes.h"   /* for codegen builds */
#endif
#include <math.h>
/* Includes_END */

/* Externs_BEGIN */
/* ====== User parameters (edit these first) ====== */
static double Kp   = 0.1;       /* PI gains (start small) */
static double Ki   = 1.0;
static double Ts   = 1e-5;       /* controller/PWM update step (s) */
static double fsw  = 10e3;      /* PWM switching frequency (Hz) */
static double Vref = 50.0;       /* voltage reference */

/* deadtime in seconds (set 0.0 to disable) */
static double deadtime_s = 100e-9;  /* e.g., 100 ns */

/* ====== Internal state ====== */
static double integ = 0.0;

static int pwm_period_counts = 0;   /* counts per PWM period */
static int dead_counts = 0;         /* deadtime in counts */
static int cnt = 0;                 /* PWM counter: 0..period-1 */
static int last_raw_u = 0;          /* last raw gate for edge detect */
static int dt_counter = 0;          /* deadtime counter */

/* helper clamp */
static double clamp(double x, double lo, double hi)
{
    if (x < lo) return lo;
    if (x > hi) return hi;
    return x;
}
/* Externs_END */

void control_Start_wrapper(void)
{
/* Start_BEGIN */
/* Start_BEGIN */
    integ = 0.0;

    /* compute integer counts per PWM period */
    if (Ts > 0.0 && fsw > 0.0) {
        pwm_period_counts = (int)floor((1.0 / (fsw * Ts)) + 0.5);
        if (pwm_period_counts < 2) pwm_period_counts = 2;
    } else {
        pwm_period_counts = 100; /* fallback */
    }

    /* deadtime in ticks */
    if (deadtime_s > 0.0 && Ts > 0.0) {
        dead_counts = (int)floor((deadtime_s / Ts) + 0.5);
        if (dead_counts < 0) dead_counts = 0;
        if (dead_counts > pwm_period_counts/2) dead_counts = pwm_period_counts/2;
    } else {
        dead_counts = 0;
    }

    cnt = 0;
    last_raw_u = 0;
    dt_counter = 0;
/* Start_END */
/* Start_END */
}

void control_Outputs_wrapper(const real_T *u0,
                             real_T *y0)
{
/* Output_BEGIN */
/* Output_BEGIN */
    /* ---- unpack inputs ----
       assume u0 = [Vin, Iin, Vout, Iout]
    */
    (void)u0[0]; /* Vin  unused in this simple test */
    (void)u0[1]; /* Iin  unused */
    const double Vout = u0[2];
    (void)u0[3]; /* Iout unused */

    /* ---- PI voltage loop -> duty ---- */
    const double e = Vref - Vout;

    integ += Ki * Ts * e;

    /* anti-windup clamp integral to keep duty sane */
    integ = clamp(integ, 0.0, 1.0);

    double duty = Kp * e + integ;

    /* avoid extreme duty that can cause issues */
    duty = clamp(duty, 0.02, 0.98);

    /* ---- PWM generation (counter-based) ---- */
    if (pwm_period_counts < 2) pwm_period_counts = 2;

    /* on-count threshold */
    int on_counts = (int)floor(duty * (double)pwm_period_counts);

    if (on_counts < 0) on_counts = 0;
    if (on_counts > pwm_period_counts) on_counts = pwm_period_counts;

    /* raw (no-deadtime) complementary gating */
    int raw_u = (cnt < on_counts) ? 1 : 0;
    int raw_l = raw_u ? 0 : 1;

    /* deadtime insertion: if an edge is detected, blank both for dead_counts ticks */
    if (dead_counts > 0) {
        if (raw_u != last_raw_u) {
            dt_counter = dead_counts;
        }

        if (dt_counter > 0) {
            /* both off during deadtime */
            y0[0] = 0.0;
            y0[1] = 0.0;
            dt_counter--;
        } else {
            y0[0] = (real_T)raw_u;
            y0[1] = (real_T)raw_l;
        }

        last_raw_u = raw_u;
    } else {
        /* no deadtime */
        y0[0] = (real_T)raw_u;
        y0[1] = (real_T)raw_l;
    }

    /* advance PWM counter */
    cnt++;
    if (cnt >= pwm_period_counts) cnt = 0;
/* Output_END */
/* Output_END */
}

void control_Terminate_wrapper(void)
{
/* Terminate_BEGIN */
/*
 * Custom Terminate code goes here.
 */
/* Terminate_END */
}